"""Running-data import and basic dynamics analysis page."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import sqlite3

import plotly.graph_objects as go
import streamlit as st
from rail_vehicle.ui import page_title

from rail_vehicle.analysis_report import (
    build_analysis_report_data,
    build_batch_comparison_report_data,
    generate_batch_comparison_report_pdf,
    generate_analysis_report_pdf,
)
from rail_vehicle.ai.context import (
    build_batch_comparison_payload,
    build_single_batch_payload,
)
from rail_vehicle.ai.ui import (
    clear_result,
    selection_fingerprint,
)
from rail_vehicle.ai.provider import input_fingerprint
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.timestamp_utils import parse_iso_timestamp
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import EVENT_DURATION_NOTE, detect_anomalies_with_anchors
from rail_vehicle.event_anchor import anchors_by_evidence
from rail_vehicle.pages.anomaly_review import render_event_review
from rail_vehicle.pages.sampling_quality import render_sampling_quality
from rail_vehicle.pages.ai_link import render_center_link, restore_source_widgets
from rail_vehicle.pages.bogie_link import batch_replay_button, event_replay_target
from rail_vehicle.workspace_resume import INPUT
from rail_vehicle.pages.data_workflow import (
    AI_REPLAY_SCOPE, ANALYSIS_REPLAY_SCOPE, ANALYSIS_TARGET, BATCH_HANDOFF, DEMO_EVIDENCE_SCOPE,
    IMPORT_VEHICLE, REPORT_COMPARISON, WORKBENCH_SOURCE,
    analysis_button, import_button, remember_workbench_selection,
)
from rail_vehicle.ai.center_state import clear_context
from rail_vehicle.curve_preview import CURVE_POINT_LIMIT, curve_preview
from rail_vehicle.replay_evidence import ReplayEvidenceScope, ReportComparison
from rail_vehicle.ui import AI, ANALYSIS, ANOMALIES, BOGIE, REPORT, plot_chart, record_analysis, source_caption, workflow_caption, navigate
from rail_vehicle.run_data import (
    MAX_CSV_SIZE_MB,
    PREVIEW_LIMIT,
    REQUIRED_COLUMNS,
    import_run_data,
    list_batch_samples,
    list_batch_sample_window,
    list_batch_vehicles,
    list_import_batches,
    list_vehicle_batches,
    validate_run_csv,
)
from rail_vehicle.vehicle_data import get_vehicle, list_vehicles


def _safe_pdf_error_message(report_kind: str, _error: Exception) -> str:
    """Return a fixed user message without exposing exception details."""
    messages = {
        "single": "分析报告生成失败，未生成下载文件。请重试；如问题持续，请联系维护人员。",
        "comparison": "批次对比报告生成失败，未生成下载文件。请重试；如问题持续，请联系维护人员。",
    }
    return messages.get(report_kind, "PDF 报告生成失败，未生成下载文件。请稍后重试。")


def _render_ai_interpretation(
    *, payload: dict, selection: dict, state_key: str, has_data: bool = True, replay_anchors=None,
    evidence_id=None, replay_scope=None,
) -> None:
    """Register current facts for the shared AI center; no provider calls here."""
    kind = "single" if state_key == "ai_single_batch_result" else "batch"
    if not has_data:
        clear_context(st.session_state, kind)
        clear_result(st.session_state, state_key)
        return
    render_center_link(kind, payload, selection, replay_anchors=replay_anchors,
                       evidence_id=evidence_id, replay_scope=replay_scope)


def _batch_actions(batch_id, vehicles, *, scope):
    if not vehicles:
        st.caption("该批次没有可打开的车辆样本。")
        return
    by_id = {vehicle["id"]: vehicle for vehicle in vehicles}
    if len(by_id) == 1:
        vehicle_id = next(iter(by_id))
        st.caption(f"车辆 {by_id[vehicle_id]['vehicle_code']} · 批次 #{batch_id}")
    else:
        st.caption("该批次包含多辆车，请选择要查看的车辆。")

        def vehicle_label(vehicle_id):
            vehicle = by_id[vehicle_id]
            return f"{vehicle['vehicle_code']} · {vehicle['sample_count']:,} 条"

        vehicle_id = st.selectbox("选择批次中的车辆", list(by_id), index=None, format_func=vehicle_label,
                                  key=f"import_{scope}_vehicle_{batch_id}")
        if vehicle_id is None:
            return
    analysis, anomalies, replay = st.columns(3, gap="small")
    if scope == "receipt":
        analysis_label, replay_label = "分析本次导入 →", "回放本次导入 →"
        suffix = "latest" if len(by_id) == 1 else "selected"
    else:
        analysis_label, replay_label, suffix = "分析历史批次 →", "回放历史批次 →", "history"
    analysis_button(analysis, label=analysis_label, key=f"import_analyze_{suffix}",
                    vehicle_id=vehicle_id, batch_id=batch_id)
    analysis_button(anomalies, label="查看该批次异常 →", key=f"import_anomalies_{suffix}",
                    vehicle_id=vehicle_id, batch_id=batch_id, page=ANOMALIES)
    batch_replay_button(replay, label=replay_label, key=f"import_bogie_{suffix}",
                        vehicle_id=vehicle_id, batch_id=batch_id)
    from rail_vehicle.pages.sensors import batch_panel
    batch_panel(vehicle_id, batch_id, key=scope)


def _render_import_history():
    st.subheader("历史导入记录")
    try:
        batches = list_import_batches()
    except (sqlite3.Error, OSError):
        st.warning("暂时无法读取历史导入记录，请检查数据库。")
        return
    if not batches:
        st.info("还没有导入记录。")
        return
    query = st.text_input("查找导入记录", placeholder="输入文件名或批次编号", key="import_history_search").strip().casefold()
    matches = [batch for batch in batches if query in batch["file_name"].casefold() or
               (query and query.removeprefix("#") == str(batch["id"]))]
    if not matches:
        st.info("没有匹配的导入记录。")
        return
    st.dataframe([{"批次编号": row["id"], "文件名": row["file_name"], "数据行数": row["row_count"],
                   "导入时间": row["imported_at"]} for row in matches[:20]], hide_index=True, width="stretch")
    if len(matches) > 20:
        st.caption(f"找到 {len(matches):,} 个批次，表格显示最近 20 个；下拉框可选全部匹配记录。")
    by_id = {row["id"]: row for row in matches}
    key = "import_history_batch"
    if st.session_state.get(key) not in by_id:
        st.session_state.pop(key, None)
    batch_id = st.selectbox("打开历史批次", list(by_id), index=None, key=key,
                            format_func=lambda value: f"#{value} · {by_id[value]['file_name']}")
    if batch_id is not None:
        try:
            vehicles = list_batch_vehicles(batch_id)
        except (sqlite3.Error, OSError):
            st.warning("暂时无法读取该批次中的车辆。")
        else:
            _batch_actions(batch_id, vehicles, scope="history")


def _render_import() -> None:
    upload_slot = st.container()
    st.caption(f"先预览并校验整份文件，再确认保存。单文件上限 {MAX_CSV_SIZE_MB} MB。")

    from rail_vehicle.demo_data import DEMO_VEHICLE_CODE, demo_csv_content
    with st.expander("下载虚构演示 CSV", expanded=False):
        st.caption(f"仅供练习，包含 120 条虚构记录。导入前请在车辆参数中登记车辆编号 {DEMO_VEHICLE_CODE}；不会自动创建车辆或导入数据库。")
        st.download_button("下载虚构演示 CSV", demo_csv_content(0).encode("utf-8"),
                           file_name="fictional_run.csv", mime="text/csv", key="download_fictional_csv")

    with st.expander("CSV 格式要求", expanded=False):
        st.markdown(
            """必需字段：`timestamp`、`vehicle_id`、`speed_kmh`、`lateral_accel`、
            `vertical_accel`。时间戳使用 ISO 8601 格式；`vehicle_id` 填写车辆参数中已有的车辆编号。
            速度范围为 0–600 km/h，加速度范围为 -100–100 m/s²。"""
        )
        st.code(
            "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
            "2026-10-08T09:30:00,RV-001,80,0.12,-0.04\n"
            "2026-10-08T09:30:01,RV-001,81,0.15,-0.02",
            language="csv",
        )

    try:
        vehicles = list_vehicles()
    except (sqlite3.Error, OSError):
        st.error("暂时无法读取已登记车辆，请检查数据库后再导入。")
        return
    if receipt := st.session_state.get("import_last_receipt"):
        with st.container(border=True):
            st.caption("本会话最近一次导入")
            st.success(f"导入成功：批次 #{receipt['batch_id']}，共保存 {receipt['row_count']:,} 行。")
            _batch_actions(receipt["batch_id"], receipt["vehicles"], scope="receipt")
    target = st.session_state.get(IMPORT_VEHICLE)
    focused_vehicle = next((vehicle for vehicle in vehicles if vehicle["id"] == target), None)
    if focused_vehicle:
        with st.container(border=True):
            st.caption("从车辆档案进入：CSV 的 `vehicle_id` 请填写对应的车辆编号。当前车辆编号：")
            st.code(focused_vehicle["vehicle_code"], language=None)
            st.caption("这只是填写提示。上传时仍按 CSV 内实际车辆编号核对，支持同一文件包含多辆已登记车辆。")
            st.download_button("下载空白 CSV 表头", (",".join(REQUIRED_COLUMNS) + "\r\n").encode("utf-8"),
                               file_name="running_data_template.csv", mime="text/csv", key="import_header_template")
            st.button("清除当前车辆提示", key="import_clear_vehicle", on_click=st.session_state.pop, args=(IMPORT_VEHICLE, None))
    if not vehicles:
        st.warning("请先在“车辆参数”页面新增车辆，再导入对应的运行数据。")
        st.button("前往车辆登记 →", key="import_register_vehicle", on_click=navigate, args=("车辆参数",))

    with upload_slot:
        uploaded_file = st.file_uploader(
            "选择运行数据 CSV 文件",
            type=["csv"],
            max_upload_size=MAX_CSV_SIZE_MB,
            key=f"run_csv_upload_{st.session_state.get('import_upload_revision', 0)}",
        )
    if uploaded_file is not None:
        validation = validate_run_csv(uploaded_file.getvalue(), vehicles)
        from rail_vehicle.pages.engineering_intake import import_preflight
        manifest, intake_ok = import_preflight(validation, revision=st.session_state.get("import_upload_revision", 0))
        if validation.preview:
            st.subheader("数据预览")
            st.dataframe(
                validation.preview,
                hide_index=True,
                width="stretch",
            )
            if validation.source_row_count > PREVIEW_LIMIT:
                st.caption(
                    f"显示前 {PREVIEW_LIMIT} 行，共 {validation.source_row_count} 行。"
                )

        if validation.errors:
            st.error("CSV 校验未通过，本文件不会写入数据库。")
            if validation.missing_by_field:
                st.write("必填字段缺值计数：" + "、".join(f"{key} {count}" for key, count in validation.missing_by_field.items()))
            for error in validation.errors:
                st.write(f"- {error}")
        else:
            if focused_vehicle and focused_vehicle["vehicle_code"] not in validation.sampling_by_vehicle:
                st.warning("该文件不含当前档案车辆。确认导入后，数据将保存到文件中实际匹配的车辆。")
            metric_columns = st.columns(3)
            metric_columns[0].metric("数据行数", f"{validation.source_row_count:,}")
            metric_columns[1].metric(
                "涉及车辆", validation.distinct_vehicle_count
            )
            metric_columns[2].metric(
                "时间范围",
                f"{validation.minimum_timestamp} — {validation.maximum_timestamp}",
            )
            if validation.sampling_by_vehicle:
                st.subheader("按车辆检查采样质量（待导入）")
                st.caption("各车分别按原 CSV 行序检查倒序；重复与间隔按 UTC 判断，避免不同车辆同一时刻被误报。"
                           "长间隔严格大于该车去重后间隔中位数的 3 倍；自动估计可能漏掉规则性缺测。"
                           "下方分析页可填写已知采样间隔。提示不阻止格式正确的数据导入，不自动删除或补点。")
                st.dataframe([
                    {"车辆编号": code, "样本数": review["sample_count"],
                     "重复时刻额外记录": review["duplicate_timestamp_rows"],
                     "原 CSV 时间倒序": review["out_of_order_pairs"],
                     "长采样间隔": review["long_gap_count"],
                     "间隔基准（秒）": review["baseline_seconds"],
                     "提示": "请回查" if review["status"] == "needs_review" else (
                         "独立时刻不足" if review["status"] == "insufficient" else "当前规则未发现问题")}
                    for code, review in validation.sampling_by_vehicle.items()
                ], hide_index=True, width="stretch")
                if any(review["status"] == "needs_review" for review in validation.sampling_by_vehicle.values()):
                    st.warning("部分车辆存在采样时间问题，建议核对原始采集记录；通过格式校验仍可导入。")
            sampling_summary = validation.sampling_summary if validation.distinct_vehicle_count == 1 else None
            if sampling_summary is None:
                st.caption("多车辆文件请以上方逐车检查为准。" if validation.distinct_vehicle_count > 1
                           else "当前文件的时间采样概览暂不可用，不影响 CSV 导入。")
            else:
                st.subheader("采样时间概览（待导入数据）")
                st.caption(
                    "重复数表示共享同一 UTC 时刻的额外记录；间隔按排序并去重后的相邻时刻计算。"
                    "此概览只描述采样分布，不影响通过校验的文件导入。"
                )
                sampling_columns = st.columns(4)
                sampling_columns[0].metric(
                    "重复时间戳记录",
                    f"{sampling_summary['duplicate_timestamp_rows']:,}",
                )
                for column, label, key in (
                    (sampling_columns[1], "最小间隔（秒）", "interval_min_seconds"),
                    (sampling_columns[2], "中位间隔（秒）", "interval_median_seconds"),
                    (sampling_columns[3], "最大间隔（秒）", "interval_max_seconds"),
                ):
                    value = sampling_summary[key]
                    column.metric(
                        label, f"{value:.3f}" if value is not None else "—"
                    )
            if intake_ok:
                st.success("格式检查通过，可以导入。")
            if st.button("导入到 SQLite", type="primary", disabled=not intake_ok):
                try:
                    batch_id = import_run_data(uploaded_file.name, validation, **(
                        {"engineering_manifest": manifest} if manifest else {}
                    ))
                except ValueError as exc:
                    st.error(str(exc))
                except (sqlite3.Error, OSError):
                    st.error("导入未完成，请先检查历史导入记录，再检查数据库目录的可写权限及磁盘空间。")
                else:
                    by_code = {vehicle["vehicle_code"]: vehicle["id"] for vehicle in vehicles}
                    members = [dict(id=by_code[code], vehicle_code=code, sample_count=review["sample_count"])
                               for code, review in validation.sampling_by_vehicle.items() if code in by_code]
                    st.session_state["import_last_receipt"] = dict(batch_id=batch_id, row_count=validation.source_row_count,
                                                                  vehicles=members)
                    # Clear the uploaded file so a widget rerun cannot submit it twice.
                    st.session_state["import_upload_revision"] = st.session_state.get("import_upload_revision", 0) + 1
                    st.rerun()

    st.divider()
    _render_import_history()


def _format_stats_table(metrics: dict, acceleration: bool = False) -> list[dict]:
    rows = [
        {"统计量": "最大值", "结果": metrics["maximum"]},
        {"统计量": "最小值", "结果": metrics["minimum"]},
        {"统计量": "平均值（算术平均）", "结果": metrics["mean"]},
    ]
    if acceleration:
        rows.append({"统计量": "RMS（均方根）", "结果": metrics["rms"]})
    return rows


_HISTORY_BOUNDS_HINT = "analysis_history_bounds_hint"
_MIN_WINDOW_BATCH_ROWS = 10_000


def _time_widget_keys(vehicle_id, batch_id):
    return tuple(f"{prefix}_{vehicle_id}_{batch_id}" for prefix in (
        "analysis_start_date", "analysis_start_time", "analysis_end_date", "analysis_end_time",
    ))


def _clear_replay_scope(vehicle_id, batch_id):
    st.session_state.pop(ANALYSIS_REPLAY_SCOPE, None)
    for key in _time_widget_keys(vehicle_id, batch_id):
        st.session_state.pop(key, None)


def _utc_window(start_date, start_time, end_date, end_time):
    # The whole-second controls include the entire final second.
    return (datetime.combine(start_date, start_time).replace(tzinfo=timezone.utc),
            datetime.combine(end_date, end_time).replace(tzinfo=timezone.utc) + timedelta(microseconds=999_999))


def _analysis_time_range(bounds, vehicle_id, batch_id):
    batch_start, batch_end = (bound.replace(tzinfo=None, microsecond=0) for bound in bounds)
    start_date_key, start_time_key, end_date_key, end_time_key = _time_widget_keys(vehicle_id, batch_id)
    for key, default in zip((start_date_key, start_time_key, end_date_key, end_time_key),
                            (batch_start.date(), batch_start.time(), batch_end.date(), batch_end.time())):
        st.session_state.setdefault(key, default)
    with st.expander("分析时间范围（UTC） · 调整筛选", expanded=False):
        start_col, end_col = st.columns(2)
        with start_col:
            start_date = st.date_input("开始日期", key=start_date_key)
            start_time = st.time_input("开始时间", step=timedelta(seconds=1), key=start_time_key)
        with end_col:
            end_date = st.date_input("结束日期", key=end_date_key)
            end_time = st.time_input("结束时间", step=timedelta(seconds=1), key=end_time_key)
    return _utc_window(start_date, start_time, end_date, end_time)


def _projected_window_request(batch_identity, batch_count):
    """Use the previous extent only as a cost hint, never as a data cache."""
    hint = st.session_state.get(_HISTORY_BOUNDS_HINT)
    if batch_count < _MIN_WINDOW_BATCH_ROWS or not hint or hint[0] != batch_identity:
        return None
    values = [st.session_state.get(key) for key in _time_widget_keys(*batch_identity)]
    if any(value is None for value in values):
        return None
    start, end = _utc_window(*values)
    previous_start, previous_end = hint[1]
    if timedelta(0) <= end - start <= (previous_end - previous_start) / 4:
        return start, end
    return None


def _analysis_timestamps(samples):
    timestamps = []
    for sample in samples:
        parsed = parse_iso_timestamp(sample["timestamp"])
        timestamps.append(parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc))
    return timestamps


def _open_demo_evidence(payload, selection, replay_anchors, evidence_id=None, replay_scope=None):
    remember_workbench_selection()
    navigate(AI, ai_center_demo=True, ai_demo_evidence=evidence_id,
             **{DEMO_EVIDENCE_SCOPE: (payload, selection, replay_anchors),
                AI_REPLAY_SCOPE: (selection, replay_scope) if replay_scope else None})


def _open_teaching_model():
    from rail_vehicle.pages.bogie import TEACH

    remember_workbench_selection()
    navigate(BOGIE, bogie_source=TEACH)


def _render_teaching_link():
    st.button("观察转向架教学模型", key="workbench_teaching_bogie", on_click=_open_teaching_model,
              icon=":material/view_in_ar:")


def _render_dynamics_analysis(*, demo: bool = False, view=ANALYSIS) -> None:
    st.session_state.pop(BATCH_HANDOFF, None)
    restored = st.session_state.pop(INPUT, None)
    if restored and restored["demo"] != demo:
        restored = None
    target = None
    if not demo:
        restore_source_widgets("single")
        target = st.session_state.pop(ANALYSIS_TARGET, None)
        if target:
            st.session_state.pop(ANALYSIS_REPLAY_SCOPE, None)
    ai_state_key = "demo_single_batch_result" if demo else "ai_single_batch_result"
    if not demo:
        clear_context(st.session_state, "single")
    source_caption(fictional=demo)
    if demo:
        from rail_vehicle.demo_data import analysis_demo_data
        demo_vehicle, demo_batches, demo_samples = analysis_demo_data()
    try:
        vehicles = [demo_vehicle] if demo else list_vehicles()
    except (sqlite3.Error, OSError):
        clear_result(st.session_state, ai_state_key)
        st.warning("暂时无法读取车辆，请检查数据库后重试。")
        return
    if not vehicles:
        clear_result(st.session_state, ai_state_key)
        st.info("请先录入车辆并导入运行数据。")
        st.caption("暂时没有 CSV 时，可以将上方数据来源切换为「内置虚构演示」。")
        st.button("前往车辆登记 →", key="analysis_register_vehicle", on_click=navigate, args=("车辆参数",))
        _render_teaching_link()
        return

    vehicle_ids = [vehicle["id"] for vehicle in vehicles]
    if target:
        if target["vehicle_id"] in vehicle_ids:
            st.session_state["dynamics_vehicle_id"] = target["vehicle_id"]
        else:
            st.session_state.pop("dynamics_vehicle_id", None)
            st.session_state.pop("dynamics_batch_id", None)
            st.warning("入口指定的车辆已不可用，请手动选择车辆。")
            target = None
    vehicle_key = "demo_dynamics_vehicle_id" if demo else "dynamics_vehicle_id"
    if restored and restored["vehicle"] in vehicle_ids:
        st.session_state[vehicle_key] = restored["vehicle"]
    selectors = st.columns(2)
    vehicle_id = selectors[0].selectbox(
        "选择车辆",
        options=vehicle_ids,
        format_func=lambda selected_id: next(
            f"{vehicle['vehicle_code']} · {vehicle['vehicle_type']}"
            for vehicle in vehicles
            if vehicle["id"] == selected_id
        ),
        key=vehicle_key,
    )
    try:
        batches = demo_batches if demo else list_vehicle_batches(vehicle_id)
    except (sqlite3.Error, OSError):
        clear_result(st.session_state, ai_state_key)
        st.warning("暂时无法读取该车辆的批次。")
        return
    if not batches:
        clear_result(st.session_state, ai_state_key)
        st.info("该车辆还没有导入运行数据。")
        st.caption("暂时没有 CSV 时，可以将上方数据来源切换为「内置虚构演示」。")
        import_button(st, label="前往 CSV 导入 →", key="analysis_import_data", vehicle_id=vehicle_id)
        _render_teaching_link()
        return

    batch_ids = [batch["id"] for batch in batches]
    if target:
        requested_batch = target["batch_id"]
        if requested_batch in batch_ids:
            st.session_state["dynamics_batch_id"] = requested_batch
        else:
            st.session_state.pop("dynamics_batch_id", None)
            if requested_batch is not None:
                st.warning("指定批次不属于这辆车或已不可用，请手动选择批次。")
    elif not demo and st.session_state.get("dynamics_batch_id") not in batch_ids:
        st.session_state.pop("dynamics_batch_id", None)
    batch_key = "demo_dynamics_batch_id" if demo else "dynamics_batch_id"
    if restored and restored["vehicle"] == vehicle_id and restored["batch"] in batch_ids:
        st.session_state[batch_key] = restored["batch"]
    batch_id = selectors[1].selectbox(
        "选择虚构批次" if demo else "选择历史导入批次",
        options=batch_ids,
        format_func=lambda selected_id: next(
            f"{batch['file_name']} · {batch['imported_at']} · "
            f"{batch['sample_count']:,} 行"
            for batch in batches
            if batch["id"] == selected_id
        ),
        key=batch_key,
    )
    batch_identity = (vehicle_id, batch_id)
    batch_row = next(batch for batch in batches if batch["id"] == batch_id)
    comparison = st.session_state.get(REPORT_COMPARISON)
    if comparison is not None and (not isinstance(comparison, ReportComparison)
                                   or not comparison.matches_batch(vehicle_id, batch_id, demo)):
        st.session_state.pop(REPORT_COMPARISON, None)
        comparison = None
        st.caption("车辆、批次或数据来源已变化；原三维对照未附入报告。")
    existing_scope = st.session_state.get(ANALYSIS_REPLAY_SCOPE)
    window_request = None
    if not demo and not target and not (existing_scope and existing_scope[0] == batch_identity):
        window_request = _projected_window_request(batch_identity, batch_row["sample_count"])

    def read_full_batch():
        try:
            rows = demo_samples[batch_id] if demo else list_batch_samples(*batch_identity)
            return rows, _analysis_timestamps(rows)
        except (sqlite3.Error, OSError):
            st.warning("暂时无法读取所选批次的样本。")
        except (KeyError, OverflowError, TypeError, ValueError):
            st.error("所选批次包含无效时间戳，无法进行时间筛选。")
        clear_result(st.session_state, ai_state_key)

    try:
        window = list_batch_sample_window(*batch_identity, *window_request) if window_request else None
    except (sqlite3.Error, OSError):
        clear_result(st.session_state, ai_state_key)
        st.warning("暂时无法读取所选批次的样本。")
        return
    if window is None:
        batch_data = read_full_batch()
        if batch_data is None:
            return
        samples, timestamps = batch_data
        if not samples:
            clear_result(st.session_state, ai_state_key)
            st.info("所选批次没有可分析的运行数据。")
            _render_teaching_link()
            return
        bounds = min(timestamps), max(timestamps)
    else:
        bounds = window.bounds
    if not demo:
        st.session_state[_HISTORY_BOUNDS_HINT] = (batch_identity, bounds)

    threshold_key = "demo_analysis_threshold" if demo else "analysis_threshold"
    if restored and (restored["vehicle"], restored["batch"]) == batch_identity:
        candidate = restored["scope"]
        if candidate is None:
            start, end = restored["start"], restored["end"]
            times = (start.date(), start.time().replace(microsecond=0), end.date(), end.time().replace(microsecond=0))
            st.session_state.update(dict(zip(_time_widget_keys(*batch_identity), times)))
            st.session_state[threshold_key] = restored["threshold"]
        elif candidate.matches(timestamps):
            st.session_state[ANALYSIS_REPLAY_SCOPE] = (batch_identity, candidate)
            st.session_state[threshold_key] = restored["threshold"]
    if target and (target["vehicle_id"], target["batch_id"]) == batch_identity:
        candidate = target.get("scope")
        if isinstance(candidate, ReplayEvidenceScope):
            if candidate.matches(timestamps):
                st.session_state[ANALYSIS_REPLAY_SCOPE] = (batch_identity, candidate)
            else:
                st.warning("三维窗口与当前批次的观测记录不匹配，请回到三维页重新选择窗口。")
        if "threshold" in target and (candidate is None or ANALYSIS_REPLAY_SCOPE in st.session_state):
            st.session_state[threshold_key] = target["threshold"]
    replay_scope = None
    stored_scope = None if demo else st.session_state.get(ANALYSIS_REPLAY_SCOPE)
    if stored_scope:
        if stored_scope[0] == batch_identity and stored_scope[1].matches(timestamps):
            replay_scope = stored_scope[1]
        else:
            st.session_state.pop(ANALYSIS_REPLAY_SCOPE, None)
    replay_rows = None
    projected = None
    if replay_scope:
        selected_start, selected_end = replay_scope.first_timestamp, replay_scope.last_timestamp
        replay_rows = [replay_scope.first_row + 1, replay_scope.last_row + 1]
        filtered_samples = samples[replay_scope.first_row:replay_scope.last_row + 1]
        st.info(f"三维回放窗口 · 批次第 {replay_scope.first_row + 1:,}–{replay_scope.last_row + 1:,} 条观测。"
                "按样本行筛选，保留同一时间戳下的具体观测。")
        st.button("返回整批分析", key="analysis_clear_replay_scope", on_click=_clear_replay_scope,
                  args=(vehicle_id, batch_id))
    else:
        selected_start, selected_end = _analysis_time_range(bounds, vehicle_id, batch_id)
        if selected_start > selected_end:
            clear_result(st.session_state, ai_state_key)
            st.warning("开始时间不能晚于结束时间，请调整筛选范围。")
            return
        if window is not None and (selected_start, selected_end) == window_request:
            projected = window
            filtered_samples = window.samples
        else:
            if window is not None:  # Streamlit may reset a time control if its default changes.
                batch_data = read_full_batch()
                if batch_data is None:
                    return
                samples, timestamps = batch_data
            filtered_samples = [sample for sample, timestamp in zip(samples, timestamps)
                                if selected_start <= timestamp <= selected_end]
    if not filtered_samples:
        clear_result(st.session_state, ai_state_key)
        st.info("所选时间范围内没有运行数据，请调整开始或结束时间。")
        return

    playback_scope = replay_scope
    if not playback_scope:
        coordinates = (dict(first_row=projected.first_row - 1, batch_count=projected.batch_count) if projected else
                       dict(first_row=sum(timestamp < selected_start for timestamp in timestamps), batch_count=len(samples)))
        playback_scope = ReplayEvidenceScope.from_analysis_slice(filtered_samples, **coordinates)

    parameter_columns = st.columns([1, 2], vertical_alignment="bottom")
    st.session_state.setdefault(threshold_key, 1.0)
    threshold = parameter_columns[0].number_input(
        "加速度绝对值阈值（m/s²）",
        min_value=0.0,
        value=None,
        step=0.1,
        key=threshold_key,
        help="同一阈值用于统计占比和异常识别；绝对值严格大于阈值时标为数据异常点。",
    )
    threshold = 1.0 if threshold is None else threshold
    if replay_scope:
        parameter_columns[1].caption(f"当前范围：{selected_start.isoformat()} — {selected_end.isoformat()}")
    else:
        parameter_columns[1].caption(f"当前范围：{selected_start:%Y-%m-%d %H:%M:%S} — {selected_end:%Y-%m-%d %H:%M:%S} UTC")

    try:
        result = analyze_run_data(filtered_samples, threshold)
    except ValueError as exc:
        clear_result(st.session_state, ai_state_key)
        st.error(str(exc))
        return
    anomaly_result, event_anchors = detect_anomalies_with_anchors(result["samples"], threshold)
    if comparison is not None:
        coordinates = {"first_row": projected.first_row, "batch_count": projected.batch_count} if projected else {}
        if not comparison.matches_scope(
            projected.samples if projected else samples,
            threshold=threshold, start=selected_start, end=selected_end, row_span=replay_rows, **coordinates,
        ):
            st.session_state.pop(REPORT_COMPARISON, None)
            comparison = None
            st.info("两条观测不再同时匹配当前范围、阈值或批次记录；本报告不会附入原三维对照。")

    st.markdown(f"**分析样本数：** {result['sample_count']:,} 条　 ·　 **异常区段（按方向）：** {len(anomaly_result['events'])} 个")
    if comparison is not None:
        st.success(f"三维对照已核对：异常第 {comparison.current.row:,} 条，参照第 {comparison.reference.row:,} 条。"
                   "可在「分析报告」查看或移除。")
    main_view = st.container()
    with st.expander("基础结果说明（无需 AI）", expanded=False):
        st.write(f"当前范围内平均速度 {result['speed']['mean']:.3f} km/h；"
                 f"横向和垂向加速度 RMS 分别为 {result['lateral_accel']['rms']:.4f}、"
                 f"{result['vertical_accel']['rms']:.4f} m/s²。")
        either = result["threshold"]["either"]
        st.write(f"在当前阈值 |a| > {threshold:.3f} m/s² 下，至少一个方向超阈值的样本"
                 f"为 {either['count']} 条（{either['percentage']:.2f}%）；"
                 f"共识别 {len(anomaly_result['events'])} 个按方向合并的异常区段。")
        st.caption("以上由当前筛选样本直接计算，不调用 AI。阈值由用户设置；统计异常和未超阈值均不构成故障、运行安全或法规结论。")
        st.markdown(
            "**计算口径：** 最大值/最小值为样本极值；平均值为算术平均；"
            "RMS = √(Σx² / n)。加速度峰值取绝对值最大点，同时显示该点的有符号原始值和时间。"
        )

    with st.expander("采样质量与报告规则"):
        sampling_overview = st.container()
        try:
            sampling_review = render_sampling_quality(
                filtered_samples, key=f"{'demo' if demo else 'history'}_sampling_{vehicle_id}_{batch_id}"
            )
        except ValueError as exc:
            clear_result(st.session_state, ai_state_key)
            st.error(str(exc))
            return

        with sampling_overview, st.expander("采样分布与数据口径", expanded=False):
            st.subheader("采样时间概览")
            st.caption(
                "以下统计仅描述当前时间筛选范围内的采样分布。重复数表示共享同一 UTC 时刻的额外记录；"
                "间隔按排序并去重后的相邻时间计算，不据此判断数据合格与否。"
            )
            sampling_columns = st.columns(4)
            sampling_columns[0].metric(
                "重复时间戳记录", f"{sampling_review['duplicate_timestamp_rows']:,}"
            )
            for column, label, key in (
                (sampling_columns[1], "最小间隔（秒）", "interval_min_seconds"),
                (sampling_columns[2], "中位间隔（秒）", "interval_median_seconds"),
                (sampling_columns[3], "最大间隔（秒）", "interval_max_seconds"),
            ):
                value = sampling_review[key]
                column.metric(label, f"{value:.3f}" if value is not None else "—")

    if view == ANALYSIS:
        _render_run_curves(main_view, result, threshold=threshold, anomaly_result=anomaly_result,
                           replay_scope=replay_scope, by_row=replay_scope is not None and selected_start == selected_end)

    selected_event = None
    if view == ANOMALIES:
        with main_view:
            selected_event = _render_anomaly_view(result, anomaly_result, threshold, vehicle_id, batch_id, selected_start,
                                                  selected_end, replay_rows, event_anchors, demo=demo,
                                                  playback_scope=playback_scope)

    selected_vehicle = next(vehicle for vehicle in vehicles if vehicle["id"] == vehicle_id)
    ai_payload = build_single_batch_payload(
        analysis=result, anomalies=anomaly_result, vehicle=selected_vehicle, batch_ref="B1",
        selected_start=selected_start.isoformat(), selected_end=selected_end.isoformat(),
    )
    ai_selection = {
        "vehicle_id": vehicle_id, "batch_id": batch_id, "selected_start": selected_start.isoformat(),
        "selected_end": selected_end.isoformat(), "threshold": threshold,
    }
    if replay_rows:
        ai_selection["row_span"] = replay_rows
    replay_anchors = anchors_by_evidence(ai_payload, event_anchors)
    evidence_id = ai_payload["anomaly_events"][selected_event]["evidence_id"] if selected_event is not None else None
    record_analysis("单批次统计分析", {"selection": ai_selection, "facts": ai_payload},
                    result["sample_count"], fictional=demo)
    event_target = None
    if selected_event is not None:
        event = anomaly_result["events"][selected_event]
        event_target = event_replay_target(
            vehicle_id=vehicle_id, batch_id=batch_id, threshold=threshold, timestamp=event["start_timestamp"],
            axis="lateral" if event["field"] == "lateral_accel" else "vertical", fictional=demo,
            row_span=replay_rows, anchor=event_anchors[selected_event], scope=playback_scope,
        )
    st.session_state[BATCH_HANDOFF] = {
        "copilot_data": (selected_vehicle, batch_row, result["samples"]),
        "event_target": event_target,
        "target": {"vehicle_id": vehicle_id, "batch_id": batch_id, "threshold": threshold, "fictional": demo,
                   **({"scope": playback_scope} if playback_scope else {})},
        "demo_evidence": (ai_payload, ai_selection, replay_anchors, evidence_id, playback_scope) if demo else None,
        "evidence_selection": ai_selection,
        "evidence_id": evidence_id,
    }
    if not demo:
        from rail_vehicle.pages.sensors import batch_panel
        batch_panel(vehicle_id, batch_id, key="analysis", analysis=result, threshold=threshold)
    with st.container(border=True):
        st.markdown("**继续处理当前批次**")
        replay, evidence, related = st.columns(3)
        with replay:
            batch_replay_button(st, label="在三维中打开当前窗口 →" if playback_scope else "在三维中回放整个批次 →",
                                key="analysis_replay_batch", scope=playback_scope, vehicle_id=vehicle_id,
                                batch_id=batch_id, threshold=threshold, fictional=demo, from_workbench=True)
        with evidence:
            if demo:
                st.button("核查当前证据（离线） →", key="workbench_demo_evidence", on_click=_open_demo_evidence,
                          args=st.session_state[BATCH_HANDOFF]["demo_evidence"], width="stretch")
            else:
                _render_ai_interpretation(payload=ai_payload, selection=ai_selection, state_key=ai_state_key,
                                          replay_anchors=replay_anchors, evidence_id=evidence_id,
                                          replay_scope=playback_scope)
        destinations = (ANALYSIS, REPORT) if view == ANOMALIES else (ANALYSIS, ANOMALIES) if view == REPORT else (ANOMALIES, REPORT)
        with related, st.container(horizontal=True):
            from rail_vehicle.pages.data_workflow import open_copilot
            st.button("在 Copilot 中分析", key="analysis_open_copilot", on_click=open_copilot)
            for destination in destinations:
                st.button(destination, key=f"batch_continue_{destination}", on_click=navigate, args=(destination,),
                          kwargs={WORKBENCH_SOURCE: "demo" if demo else "history"})

    if view == REPORT:
        with main_view:
            _render_report_view(vehicles, selected_vehicle, vehicle_id, batch_id, batches, selected_start,
                                selected_end, result, anomaly_result, sampling_review, replay_rows, comparison, threshold,
                                ai_payload, ai_selection, demo=demo)


def _render_run_curves(container, result, *, threshold, anomaly_result, replay_scope, by_row):
    with container:
        st.subheader("速度统计")
        speed_columns = st.columns(3)
        speed_columns[0].metric("最大速度（km/h）", f"{result['speed']['maximum']:.3f}", border=True)
        speed_columns[1].metric("最小速度（km/h）", f"{result['speed']['minimum']:.3f}", border=True)
        speed_columns[2].metric("平均速度（km/h）", f"{result['speed']['mean']:.3f}", border=True)

        st.subheader("运行曲线")
        x_field, x_title = ("batch_row", "批次样本序号") if by_row else ("timestamp", "时间")
        chart_specs = (
            ("speed_kmh", f"速度—{x_title}", "速度（km/h）"),
            ("lateral_accel", f"横向加速度—{x_title}", "横向加速度（m/s²）"),
            ("vertical_accel", f"垂向加速度—{x_title}", "垂向加速度（m/s²）"),
        )
        ordered_samples = result["samples"]
        if by_row:
            st.caption("窗口内样本处于同一 UTC 时刻，按批次样本序号分开显示各条读值。")
            ordered_samples = [dict(sample, batch_row=row) for row, sample in
                               enumerate(ordered_samples, start=replay_scope.first_row + 1)]
        if len(ordered_samples) > CURVE_POINT_LIMIT:
            st.caption(f"长批次曲线预览：每条曲线或异常标记序列最多显示 {CURVE_POINT_LIMIT:,} 个原始点，"
                       "分段保留极值。缩小时间范围可查看细节；统计、事件、证据和 PDF 输入仍使用全部筛选样本。")
        curve_tabs = st.tabs([spec[1] for spec in chart_specs])
        for curve_tab, (field, title, y_title) in zip(curve_tabs, chart_specs):
            preview = curve_preview(ordered_samples, field)
            figure = go.Figure(
                data=[
                    go.Scatter(
                        x=[sample[x_field] for sample in preview],
                        y=[sample[field] for sample in preview],
                        mode="markers" if by_row else "lines",
                        name=y_title,
                        hovertemplate="%{x}<br>%{y:.4f}<extra></extra>",
                    )
                ]
            )
            figure.update_layout(
                title=title,
                xaxis_title=x_title,
                yaxis_title=y_title,
                margin={"l": 20, "r": 20, "t": 50, "b": 20},
            )
            if by_row:
                figure.update_xaxes(tickformat="d", dtick=max(1, (len(ordered_samples) + 8) // 10),
                                    tick0=replay_scope.first_row + 1)
            if field in ("lateral_accel", "vertical_accel"):
                axis_points = [
                    point
                    for point in anomaly_result["points"]
                    if point["field"] == field
                ]
                if axis_points:
                    if by_row:
                        flagged_rows = (sample["batch_row"] for sample in ordered_samples
                                        if abs(sample[field]) > threshold)
                        axis_points = [dict(point, batch_row=row) for point, row in
                                       zip(axis_points, flagged_rows, strict=True)]
                    axis_points = curve_preview(axis_points, "value")
                    figure.add_trace(
                        go.Scatter(
                            x=[point[x_field] for point in axis_points],
                            y=[point["value"] for point in axis_points],
                            mode="markers",
                            name="统计异常点",
                            marker={"color": "#d62728", "size": 10, "symbol": "diamond"},
                            customdata=[point["type"] for point in axis_points],
                            hovertemplate="%{x}<br>%{customdata}<br>%{y:.4f} m/s²<extra></extra>",
                        )
                    )
                figure.add_hline(
                    y=threshold,
                    line_dash="dash",
                    line_color="#d62728",
                    annotation_text="+阈值",
                )
                figure.add_hline(
                    y=-threshold,
                    line_dash="dash",
                    line_color="#d62728",
                    annotation_text="−阈值",
                )
            with curve_tab:
                plot_chart(figure, width="stretch", key=f"chart_{field}")

        st.subheader("加速度统计")
        lateral_col, vertical_col = st.columns(2)
        with lateral_col:
            st.markdown("**横向加速度（m/s²）**")
            st.dataframe(
                _format_stats_table(result["lateral_accel"], acceleration=True),
                hide_index=True,
                width="stretch",
            )
        with vertical_col:
            st.markdown("**垂向加速度（m/s²）**")
            st.dataframe(
                _format_stats_table(result["vertical_accel"], acceleration=True),
                hide_index=True,
                width="stretch",
            )

        peak_columns = st.columns(2)
        for column, label, stats in (
            (peak_columns[0], "横向", result["lateral_accel"]),
            (peak_columns[1], "垂向", result["vertical_accel"]),
        ):
            column.info(
                f"{label}加速度绝对峰值：{stats['peak_absolute']:.4f} m/s²\n\n"
                f"该点有符号值：{stats['peak_signed']:.4f} m/s²\n\n"
                f"对应时间：{stats['peak_timestamp']}"
            )

        st.subheader("加速度阈值统计")
        st.caption(
            f"阈值：|加速度| > {result['threshold']['value']:.3f} m/s²；"
            "横向和垂向分别以样本行数为分母，合计按至少一个方向超过阈值的样本行去重。"
        )
        exceedance = result["threshold"]
        st.dataframe(
            [
                {
                    "统计范围": label,
                    "超过阈值样本数": values["count"],
                    "样本占比": f"{values['percentage']:.2f}%",
                }
                for label, values in (
                    ("横向加速度", exceedance["lateral"]),
                    ("垂向加速度", exceedance["vertical"]),
                    ("任一方向（去重）", exceedance["either"]),
                )
            ],
            hide_index=True,
            width="stretch",
        )


def _render_anomaly_view(result, anomaly_result, threshold, vehicle_id, batch_id, selected_start,
                         selected_end, replay_rows, event_anchors, *, demo, playback_scope):
    with st.container():
        st.subheader("数据异常检测")
        st.caption(
            f"规则：横向或垂向加速度绝对值严格大于 {threshold:.3f} m/s² 时标记为统计异常点。"
            "同一方向相邻异常样本合并为一个区段，遇到正常样本则分段。"
            "异常区段仅描述数据/统计异常，不用于判断车辆故障或安全事故。"
        )
        st.caption(EVENT_DURATION_NOTE)
        anomaly_columns = st.columns(2)
        anomaly_columns[0].metric("数据异常点", len(anomaly_result["points"]), border=True)
        anomaly_columns[1].metric("统计异常区段", len(anomaly_result["events"]), border=True)
        event_index = render_event_review(result["samples"], anomaly_result, {
            "vehicle_id": vehicle_id, "batch_id": batch_id,
            "start": selected_start.isoformat(), "end": selected_end.isoformat(),
            **({"row_span": replay_rows} if replay_rows else {}),
        }, allow_bogie=True, fictional=demo, anchors=event_anchors, playback_scope=playback_scope)
        if anomaly_result["events"]:
            with st.expander("查看全部异常区段"):
                st.dataframe(
                    [{
                        "异常类型": event["type"],
                        "开始时间": event["start_timestamp"],
                        "结束时间": event["end_timestamp"],
                        "首末样本间隔（秒）": f"{event['duration_seconds']:.3f}",
                        "区段最大绝对值（m/s²）": f"{event['maximum_absolute_value']:.4f}",
                        "对应有符号值（m/s²）": f"{event['maximum_signed_value']:.4f}",
                        "峰值时间": event["maximum_timestamp"],
                        "异常样本数": event["sample_count"],
                    } for event in anomaly_result["events"]],
                    hide_index=True,
                    width="stretch",
                )
        else:
            st.info("当前批次未发现超过所设阈值的数据异常点。")
        return event_index

def _render_report_view(vehicles, selected_vehicle, vehicle_id, batch_id, batches, selected_start,
                        selected_end, result, anomaly_result, sampling_review, replay_rows, comparison, threshold,
                        ai_payload, ai_selection, *, demo):
    from rail_vehicle.engineering_intake import report_provenance
    with st.container(border=True):
        st.subheader("导出分析报告")
        st.caption("PDF 报告包含当前车辆、批次、时间范围、统计、异常事件、采样质量检查与规则及曲线。"
                   "调整采样检查规则后需重新生成 PDF。")
        comparison_section = asdict(comparison) if comparison is not None else None
        if comparison is not None:
            st.caption("报告将附上这两条原始观测与对照条件；三维模型姿态不会写入报告。")
            st.button("移除报告中的三维对照", key="analysis_remove_report_comparison",
                      on_click=st.session_state.pop, args=(REPORT_COMPARISON, None))
        current_ai_result = None if demo else st.session_state.get("ai_single_batch_result")
        reference = None
        snapshot = {}
        if not demo:
            from rail_vehicle.calibration_check import REFERENCE_STATUS, matching_reference
            from rail_vehicle.pages.sensors import REFERENCE_SESSION_KEY, read_snapshot
            snapshot = read_snapshot(vehicle_id, batch_id)
            saved_reference = st.session_state.get(REFERENCE_SESSION_KEY)
            if saved_reference:
                reference = matching_reference(saved_reference, snapshot, result["samples"])
                if reference:
                    st.caption(f"标定复核将附入 PDF · {reference['channel']} · "
                               f"配置 v{reference['configuration']['version']} · {REFERENCE_STATUS[reference['status']]}。")
                else:
                    st.info("最近一次标定复核与当前批次、范围或配置版本不一致，报告将使用当前运行分析。")
        selected_batch = next(batch for batch in batches if batch["id"] == batch_id)
        provenance = report_provenance(snapshot, fallback="内置虚构演示" if demo else "历史导入 · 数据来源未声明",
                                       source_sha256=selected_batch.get("source_sha256"))
        st.markdown("**报告内容预览**")
        st.write(f"{'虚构演示' if demo else '历史导入'} · 车辆 {selected_vehicle['vehicle_code']} · 批次 #{batch_id}")
        st.text(selected_batch["file_name"])
        st.caption(f"工程证据 · {provenance['source']} · 独立实测验收待完成")
        if replay_rows:
            st.write(f"报告范围：批次第 {replay_rows[0]:,}–{replay_rows[1]:,} 条观测（按样本行）")
            range_label = "窗口时间（UTC）"
            timespec = "auto"
        else:
            range_label = "报告时间筛选（UTC，包含结束秒内的记录）"
            timespec = "seconds"
        start_text = selected_start.replace(tzinfo=None).isoformat(sep=" ", timespec=timespec)
        end_text = selected_end.replace(tzinfo=None).isoformat(sep=" ", timespec=timespec)
        st.caption(f"{range_label}：{start_text} 至 {end_text}")
        st.write(f"{result['sample_count']:,} 条观测 · {len(anomaly_result['events']):,} 个规则异常区段")
        st.caption(f"异常规则：加速度绝对值大于 {threshold:g} m/s²。")
        st.caption("包含运行统计、曲线、异常记录和采样质量。AI 解读仅在与本报告证据及范围完全匹配时附入。")
        report_state_key = "demo_analysis_report_key" if demo else "analysis_report_key"
        report_pdf_key = "demo_analysis_report_pdf" if demo else "analysis_report_pdf"
        current_ai_pdf_fingerprint = (
            selection_fingerprint(current_ai_result)
            if isinstance(current_ai_result, dict)
            and current_ai_result.get("status") == "available"
            else "no-ai-result"
        )
        generate = st.button("生成 PDF 分析报告", type="primary",
                             key="demo_generate_analysis_report" if demo else "generate_analysis_report")
        report_key = selection_fingerprint({
            "analysis": ai_payload, "selection": ai_selection,
            "sampling_quality": sampling_review, "ai_result": current_ai_pdf_fingerprint,
            "vehicle": selected_vehicle, "samples": result["samples"], "comparison": comparison_section,
            "calibration_reference": reference,
            "provenance": provenance,
        }) if generate or st.session_state.get(report_pdf_key) else None
        if st.session_state.get(report_state_key) != report_key:
            st.session_state.pop(report_state_key, None)
            st.session_state.pop(report_pdf_key, None)

        if generate:
            try:
                selected_vehicle = selected_vehicle if demo else get_vehicle(vehicle_id)
                if selected_vehicle is None:
                    raise ValueError("找不到所选车辆，请刷新页面后重试。")
                report_data = build_analysis_report_data(
                    vehicle=selected_vehicle,
                    batch=selected_batch,
                    analysis=result,
                    anomalies=anomaly_result,
                    selected_start=selected_start,
                    selected_end=selected_end,
                    ai_context=ai_payload,
                    ai_result=current_ai_result,
                    ai_selection_fingerprint=selection_fingerprint(ai_selection),
                    sampling_expected_interval_seconds=(
                        sampling_review["baseline_seconds"]
                        if sampling_review["baseline_source"] == "specified" else None
                    ),
                    sampling_gap_multiplier=sampling_review["gap_multiplier"],
                    report_comparison=comparison_section,
                    calibration_reference=reference,
                    provenance=provenance,
                    **({"replay_rows": replay_rows} if replay_rows else {}),
                )
                pdf_bytes = generate_analysis_report_pdf(report_data)
            except Exception as exc:
                st.error(_safe_pdf_error_message("single", exc))
            else:
                st.session_state[report_state_key] = report_key
                st.session_state[report_pdf_key] = pdf_bytes
                st.success("分析报告已生成，可以下载 PDF 文件。")

        if (
            st.session_state.get(report_state_key) == report_key
            and st.session_state.get(report_pdf_key)
        ):
            selected_vehicle_code = next(
                vehicle["vehicle_code"]
                for vehicle in vehicles
                if vehicle["id"] == vehicle_id
            )
            safe_vehicle_code = "".join(
                character for character in selected_vehicle_code
                if character.isalnum() or character in "-_"
            ) or "vehicle"
            st.download_button(
                "下载 PDF 报告",
                data=st.session_state[report_pdf_key],
                file_name=f"rail_vehicle_report_{safe_vehicle_code}_batch_{batch_id}.pdf",
                mime="application/pdf",
                key="demo_download_analysis_report" if demo else "download_analysis_report",
            )


def render_statistics_demo(*, view=ANALYSIS) -> None:
    """Offline, explicitly fictional entry into the existing analysis workflow."""
    st.session_state.pop(BATCH_HANDOFF, None)
    page_title(view if view != ANALYSIS else "虚构数据分析", "数据来源：内置虚构演示", primary=True)
    st.info("内置虚构数据 · 两个 120 条记录的批次，未导入数据库，不代表真实车辆。"
            "仅演示基础动力学统计与规则异常，不判断车辆故障或运行安全。")
    st.caption("默认阈值 1 m/s²：第 30–32 秒有横向变化，第 70–71 秒有垂向变化；虚构批次 2 在第 93–94 秒有双轴同点变化。"
               "点击计算后可筛选 UTC 时间、调整阈值、回查事件并导出带虚构来源标识的 PDF；不会读取 AI 配置。")
    if st.button("计算虚构运行分析", key="statistics_demo_calculate", type="primary"):
        st.session_state["statistics_demo_started"] = True
    if st.session_state.get("statistics_demo_started"):
        _render_dynamics_analysis(demo=True, view=view)


def _render_batch_comparison() -> None:
    restore_source_widgets("batch")
    clear_context(st.session_state, "batch")
    st.caption(
        "选择同一车辆的两个或多个历史批次；对比曲线按各批次首条样本对齐，横轴为相对运行时间（秒）。"
    )
    vehicles = list_vehicles()
    if not vehicles:
        clear_result(st.session_state, "ai_batch_comparison_result")
        st.info("请先录入车辆并导入运行数据。")
        return

    vehicle_id = st.selectbox(
        "选择对比车辆",
        options=[vehicle["id"] for vehicle in vehicles],
        format_func=lambda selected_id: next(
            f"{vehicle['vehicle_code']} · {vehicle['vehicle_type']}"
            for vehicle in vehicles
            if vehicle["id"] == selected_id
        ),
        key="comparison_vehicle_id",
    )
    batches = list_vehicle_batches(vehicle_id)
    if len(batches) < 2:
        clear_result(st.session_state, "ai_batch_comparison_result")
        st.info("该车辆至少需要两个历史导入批次才能进行对比。")
        return

    batch_by_id = {batch["id"]: batch for batch in batches}
    default_ids = [batch["id"] for batch in batches[:2]]
    selected_ids = st.multiselect(
        "选择两个或多个历史批次",
        options=list(batch_by_id),
        default=default_ids,
        format_func=lambda selected_id: (
            f"{batch_by_id[selected_id]['file_name']} · "
            f"{batch_by_id[selected_id]['imported_at']} · "
            f"{batch_by_id[selected_id]['sample_count']:,} 行"
        ),
        key=f"comparison_batch_ids_{vehicle_id}",
    )
    if len(selected_ids) < 2:
        clear_result(st.session_state, "ai_batch_comparison_result")
        st.info("请选择至少两个批次以显示对比结果。")
        return

    comparison_inputs = []
    for batch_id in selected_ids:
        batch = batch_by_id[batch_id]
        comparison_inputs.append(
            {
                "batch_id": batch_id,
                "label": f"{batch['file_name']} · #{batch_id}",
                "samples": list_batch_samples(vehicle_id, batch_id),
            }
        )
    try:
        comparisons = compare_run_batches(comparison_inputs)
    except ValueError as exc:
        clear_result(st.session_state, "ai_batch_comparison_result")
        st.error(f"无法完成批次对比：{exc}")
        return

    record_analysis("多批次对比", {"vehicle": vehicle_id, "facts": [{key: value for key, value in row.items() if key != "series"} for row in comparisons]}, sum(row["sample_count"] for row in comparisons))
    st.subheader("批次对比指标")
    st.caption(
        "平均速度、最大速度单位为 km/h；加速度 RMS 和绝对峰值单位为 m/s²。"
        "RMS 与峰值均按每个批次的全部样本计算。"
    )
    st.dataframe(
        [
            {
                "运行批次": result["label"],
                "数据点数量": result["sample_count"],
                "平均速度（km/h）": f"{result['mean_speed_kmh']:.3f}",
                "最大速度（km/h）": f"{result['maximum_speed_kmh']:.3f}",
                "横向加速度 RMS（m/s²）": f"{result['lateral_accel_rms']:.4f}",
                "垂向加速度 RMS（m/s²）": f"{result['vertical_accel_rms']:.4f}",
                "横向加速度绝对峰值（m/s²）": f"{result['lateral_accel_peak_absolute']:.4f}",
                "垂向加速度绝对峰值（m/s²）": f"{result['vertical_accel_peak_absolute']:.4f}",
            }
            for result in comparisons
        ],
        hide_index=True,
        width="stretch",
    )

    st.subheader("批次采样时间概览")
    st.caption(
        "间隔按各批次内部排序并去重后的相邻 UTC 时刻计算；这些指标仅描述采样分布，不判断数据是否合格。"
    )
    st.dataframe(
        [
            {
                "运行批次": result["label"],
                "重复时间戳记录": result["sampling_summary"]["duplicate_timestamp_rows"],
                "最小间隔（秒）": (
                    f"{result['sampling_summary']['interval_min_seconds']:.3f}"
                    if result["sampling_summary"]["interval_min_seconds"] is not None
                    else "—"
                ),
                "中位间隔（秒）": (
                    f"{result['sampling_summary']['interval_median_seconds']:.3f}"
                    if result["sampling_summary"]["interval_median_seconds"] is not None
                    else "—"
                ),
                "最大间隔（秒）": (
                    f"{result['sampling_summary']['interval_max_seconds']:.3f}"
                    if result["sampling_summary"]["interval_max_seconds"] is not None
                    else "—"
                ),
            }
            for result in comparisons
        ],
        hide_index=True,
        width="stretch",
    )

    chart_specs = (
        ("speed_kmh", "速度批次对比", "速度（km/h）"),
        ("lateral_accel", "横向加速度批次对比", "横向加速度（m/s²）"),
        ("vertical_accel", "垂向加速度批次对比", "垂向加速度（m/s²）"),
    )
    for field, title, y_title in chart_specs:
        figure = go.Figure()
        for result in comparisons:
            figure.add_trace(
                go.Scatter(
                    x=[point["relative_time_seconds"] for point in result["series"]],
                    y=[point[field] for point in result["series"]],
                    mode="lines",
                    name=result["label"],
                    hovertemplate="相对运行时间：%{x:.3f} 秒<br>%{y:.4f}<extra>%{fullData.name}</extra>",
                )
            )
        figure.update_layout(
            title=title,
            xaxis_title="相对运行时间（秒）",
            yaxis_title=y_title,
            margin={"l": 20, "r": 20, "t": 50, "b": 20},
        )
        plot_chart(figure, width="stretch", key=f"comparison_{field}")

    ai_payload = build_batch_comparison_payload(comparisons)
    ai_selection = {"vehicle_id": vehicle_id, "batch_ids": selected_ids}
    _render_ai_interpretation(
        payload=ai_payload,
        selection=ai_selection,
        state_key="ai_batch_comparison_result",
    )

    st.subheader("导出批次对比报告")
    st.caption("报告包含当前批次对比指标、三张相对时间曲线，以及与当前对比输入 fingerprint 匹配的 AI 解读（如果已生成）。")
    current_ai_result = st.session_state.get("ai_batch_comparison_result")
    current_ai_pdf_fingerprint = (
        selection_fingerprint(current_ai_result)
        if isinstance(current_ai_result, dict)
        and current_ai_result.get("status") == "available"
        else "no-ai-result"
    )
    comparison_report_key = (
        f"{input_fingerprint(ai_payload)}:{selection_fingerprint(ai_selection)}:"
        f"{current_ai_pdf_fingerprint}"
    )
    if st.session_state.get("comparison_report_key") != comparison_report_key:
        st.session_state.pop("comparison_report_key", None)
        st.session_state.pop("comparison_report_pdf", None)
    if st.button("生成批次对比 PDF 报告", key="generate_comparison_report"):
        try:
            report_data = build_batch_comparison_report_data(
                comparisons,
                ai_context=ai_payload,
                ai_result=st.session_state.get("ai_batch_comparison_result"),
                ai_selection_fingerprint=selection_fingerprint(ai_selection),
            )
            pdf_bytes = generate_batch_comparison_report_pdf(report_data)
        except Exception as exc:
            st.error(_safe_pdf_error_message("comparison", exc))
        else:
            st.session_state["comparison_report_key"] = comparison_report_key
            st.session_state["comparison_report_pdf"] = pdf_bytes
            st.success("批次对比报告已生成，可以下载 PDF 文件。")
    if (
        st.session_state.get("comparison_report_key") == comparison_report_key
        and st.session_state.get("comparison_report_pdf")
    ):
        st.download_button(
            "下载批次对比 PDF 报告",
            data=st.session_state["comparison_report_pdf"],
            file_name="rail_vehicle_batch_comparison_report.pdf",
            mime="application/pdf",
            key="download_comparison_report",
        )


def render_comparison() -> None:
    page_title("批次对比", "按相对运行时间比较同一车辆的多个历史批次", primary=True)
    _render_batch_comparison()


def render(*, view=ANALYSIS) -> None:
    page_title(view, {
        ANALYSIS: "查看当前范围的速度、横向与垂向加速度曲线及统计。",
        ANOMALIES: "按阈值核查异常区段和原始观测，定位到三维回放。",
        REPORT: "确认车辆、范围与报告内容，再生成和下载 PDF。",
    }[view], primary=True)
    control = "workbench_source_control"
    st.session_state[control] = st.session_state.get(WORKBENCH_SOURCE, "history")

    def select_source():
        st.session_state[WORKBENCH_SOURCE] = st.session_state[control]
        clear_context(st.session_state, "single")
        clear_result(st.session_state, "ai_single_batch_result")

    source = st.radio("数据来源", ("history", "demo"), key=control, horizontal=True, on_change=select_source,
                      format_func={"history": "历史导入批次", "demo": "内置虚构演示"}.get)
    if source == "demo":
        st.caption("演示数据不会写入数据库。要使用自己的 CSV，请切回「历史导入批次」。")
    _render_dynamics_analysis(demo=source == "demo", view=view)
