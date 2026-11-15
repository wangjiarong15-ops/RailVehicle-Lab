"""Opt-in AI interpretation and local review of saved evidence snapshots."""

from dataclasses import replace
from datetime import datetime, timezone
from sqlite3 import Error as DatabaseError

import streamlit as st

from rail_vehicle.ai.center_state import CONTEXTS, current_context
from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.evidence_view import evidence_detail_catalog
from rail_vehicle.ai.history import delete_history, list_history, load_history, save_history
from rail_vehicle.ai.openai_provider import create_configured_provider
from rail_vehicle.ai.ui import (ai_configuration_message, clear_result, invalidate_stale_result,
                               render_validated_result, request_ai_analysis, selection_fingerprint,
                               status_message)
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.pages.bogie_link import event_replay_button
from rail_vehicle.pages.data_workflow import (
    DEMO_EVIDENCE_SCOPE, WORKBENCH_RETURN, WORKBENCH_SOURCE, render_workbench_return, return_to_workbench,
)
from rail_vehicle.ui import ANALYSIS_MODE, navigate, page_title


_HISTORY_LABELS = {analysis_type: label for label, analysis_type, _ in CONTEXTS.values()}


def _render_history():
    with st.expander("本会话 AI 分析记录"):
        history = st.session_state.get("ai_center_history", ())
        if not history:
            st.caption("本会话还没有通过校验的 AI 解读。")
        for label, metadata in history:
            st.write(f"{label} · {metadata['provider']} / {metadata['model']}")
            st.caption(metadata["generated_at"])
        if history:
            st.caption("仅保留最近 10 次通过校验的生成记录。")


def _event_button(event, selection, key, label):
    event_replay_button(label=label, key=key, vehicle_id=selection["vehicle_id"],
                        batch_id=selection["batch_id"], threshold=selection["threshold"],
                        timestamp=event["start_timestamp"], axis=event["type"], row_span=selection.get("row_span"))


def _cited_event_links(analysis, events, selection, *, key_prefix="ai_center"):
    claims = [analysis["summary"]]
    for section in ("observations", "possible_causes", "further_checks", "limitations"):
        claims.extend(analysis[section])
    cited = {evidence_id for claim in claims for evidence_id in claim["evidence_ids"]}
    matches = {event["evidence_id"]: event for event in events if event["evidence_id"] in cited}
    if matches:
        evidence_id = st.selectbox("AI 解读引用的异常区段", list(matches), key=f"{key_prefix}_cited_event")
        _event_button(matches[evidence_id], selection, f"{key_prefix}_cited_event_jump", "回放 AI 引用的统计区段")
        st.caption("定位依据为原统计区段。高亮表示关联结构，不能确认部件故障。")
    else:
        st.caption("本次 AI 解读未引用可定位的异常区段。")


def _render_archived_scope(payload, selection, record):
    kind = payload["analysis_type"]
    if record["data_source"] == "demo":
        st.info("此记录来自内置虚构实验，不是实际车辆测量。")
    if kind == "single_batch":
        st.caption(f"保存时的分析范围：{selection['selected_start']} 至 {selection['selected_end']}")
        st.caption(f"加速度阈值：{selection['threshold']:g} m/s²")
        if "row_span" in selection:
            first, last = selection["row_span"]
            st.caption(f"三维回放窗口：批次第 {first:,}–{last:,} 条观测。")
    mapping = []
    if kind == "batch_comparison":
        mapping = [(selection["vehicle_id"], batch) for batch in selection["batch_ids"]]
    elif record["data_source"] == "history" and kind != "single_batch":
        local = selection["selection"]
        if kind == "ml_experiment":
            mapping = [(local["vehicle"], local["reference"]), (local["vehicle"], local["target"])]
        elif kind == "vibration_experiment":
            mapping = [(local["vehicle"], local["batch"])]
        elif kind == "fleet_comparison":
            mapping = [(entry["vehicle_id"], entry["batch_id"]) for entry in local["entries"]]
    if mapping:
        with st.expander("匿名批次对应的本机记录"):
            st.dataframe([{"证据批次": f"B{index}", "车辆 ID": vehicle, "批次 ID": batch}
                          for index, (vehicle, batch) in enumerate(mapping, 1)], hide_index=True)
    ranges = payload.get("experiment", {}).get("ranges", ())
    if ranges:
        with st.expander("保存时的实验时间范围"):
            st.dataframe([{"证据批次": row["batch_ref"], "开始": row["start"], "结束": row["end"]}
                          for row in ranges], hide_index=True)


def _render_archived_evidence(payload, record_id):
    catalog = evidence_detail_catalog(payload)
    if not catalog:
        return
    with st.expander("查看全部已保存证据"):
        st.caption("这些是保存时的 Python 计算结果，包括 AI 没有引用的指标。")
        evidence_id = st.selectbox("快照证据 ID", list(catalog), key=f"ai_history_evidence_{record_id}")
        st.dataframe(catalog[evidence_id], hide_index=True, width="stretch")


def _render_local_history():
    st.subheader("本机保存的 AI 解读")
    st.caption("这里展示保存当时的证据快照。查看或删除不会调用 AI；三维跳转会在当前批次中重新查找对应样本。")
    history_filter = st.selectbox("筛选分析类型", ["all", *_HISTORY_LABELS],
                                  format_func=lambda value: _HISTORY_LABELS.get(value, "全部类型"),
                                  key="ai_history_filter")
    analysis_type = None if history_filter == "all" else history_filter
    page_key = f"ai_local_history_page_{history_filter}"
    try:
        records, page, last = list_history(st.session_state.get(page_key, 0), analysis_type=analysis_type)
    except (DatabaseError, OSError):
        st.warning("暂时无法读取本机历史。当前会话的分析和报告不受影响。")
        return
    st.session_state[page_key] = page
    if st.session_state.pop("ai_local_history_deleted", False):
        st.success("所选历史记录已删除。")
    if not records:
        st.info("当前类型没有已保存的解读。" if analysis_type else
                "本机还没有保存的解读。生成通过校验的 AI 解读后，可点击“保存到本机历史”。")
        return
    if last:
        previous, position, following = st.columns([1, 2, 1])
        with previous:
            if st.button("上一页", key="ai_history_prev", disabled=page == 0):
                st.session_state[page_key] = page - 1
                st.rerun()
        position.caption(f"第 {page + 1} / {last + 1} 页")
        with following:
            if st.button("下一页", key="ai_history_next", disabled=page == last):
                st.session_state[page_key] = page + 1
                st.rerun()
    by_id = {record["id"]: record for record in records}

    def label(record_id):
        row = by_id[record_id]
        date = row["generated_at"][:19].replace("T", " ")
        kind = _HISTORY_LABELS.get(row["analysis_type"], "未知类型")
        origin = "虚构实验" if row["data_source"] == "demo" else "历史批次"
        vehicle = f" · 车辆 #{row['vehicle_id']}" if row["vehicle_id"] else ""
        batch = f" / 批次 #{row['batch_id']}" if row["batch_id"] else ""
        return f"#{record_id} · {kind} · {origin}{vehicle}{batch} · {date} UTC"

    record_key = f"ai_history_record_{history_filter}_{page}"
    if st.session_state.get(record_key) not in by_id:
        st.session_state[record_key] = next(iter(by_id))
    record_id = st.selectbox("保存的分析记录", list(by_id), format_func=label, key=record_key)
    with st.expander("管理此记录"):
        confirmed = st.checkbox("确认只删除这条 AI 解读及其证据快照", key=f"ai_history_confirm_{record_id}")
        if st.button("删除所选记录", key=f"ai_history_delete_{record_id}", disabled=not confirmed):
            try:
                delete_history(record_id)
            except (DatabaseError, OSError):
                st.warning("暂时无法删除，请检查本机数据库权限后重试。")
            else:
                st.session_state["ai_local_history_deleted"] = True
                st.rerun()
    try:
        saved = load_history(record_id)
    except (DatabaseError, OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        st.warning("这条历史记录无法读取或证据校验未通过，已阻止显示。")
        return
    if saved is None:
        st.info("所选记录已不存在，请选择其他记录。")
        return
    payload, selection, result = saved
    st.caption(f"Provider：{by_id[record_id]['provider']}")
    _render_archived_scope(payload, selection, by_id[record_id])
    if render_validated_result(result, payload, st):
        if payload["analysis_type"] == "single_batch":
            _cited_event_links(result["analysis"], payload["anomaly_events"], selection,
                               key_prefix=f"ai_history_{record_id}")
        else:
            st.caption("这份证据没有可直接定位的单批次阈值区段，无法跳转至三维样本。")
        _render_archived_evidence(payload, record_id)


def _render_save_history(payload, selection, result):
    st.caption("保存后可离线回查。仅写入本机 SQLite，包括当前范围、聚合证据和通过校验的解读。")
    if st.button("保存到本机历史", key="ai_center_save_history"):
        try:
            created = save_history(payload, selection, result)
        except (DatabaseError, OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            st.warning("本次解读未能写入本机历史。当前会话的解读仍可查看。")
        else:
            st.success("已保存到本机历史。" if created else "这次解读已在本机历史中。")


def _render_demo_evidence():
    from rail_vehicle.ai.context import build_single_batch_payload
    from rail_vehicle.anomaly import detect_anomalies
    from rail_vehicle.demo_data import analysis_demo_data
    from rail_vehicle.dynamics import analyze_run_data

    st.info("虚构数据证据演示 · 数值由 Python 计算；本演示未生成 AI 解读，也不读取 AI 配置。")
    scoped = st.session_state.get(DEMO_EVIDENCE_SCOPE)
    if scoped:
        payload, selection = scoped
        vehicle_id, batch, threshold = (selection[name] for name in ("vehicle_id", "batch_id", "threshold"))
        st.caption(f"虚构批次 {-batch} · 阈值 {threshold:g} m/s² · 当前分析范围："
                   f"{selection['selected_start']} — {selection['selected_end']}")
    else:
        vehicle, batches, samples = analysis_demo_data()
        vehicle_id = vehicle["id"]
        batch = st.selectbox("虚构证据批次", [item["id"] for item in batches],
                             format_func=lambda value: f"虚构批次 {-value}", key="ai_demo_batch")
        if "ai_demo_threshold" not in st.session_state:
            st.session_state["ai_demo_threshold"] = 1.0
        threshold = st.number_input("加速度绝对值阈值（m/s²）", min_value=0.0, value=None,
                                    step=0.1, key="ai_demo_threshold")
        threshold = 1.0 if threshold is None else threshold
        analysis = analyze_run_data(samples[batch], threshold)
        anomalies = detect_anomalies(analysis["samples"], threshold)
        payload = build_single_batch_payload(analysis=analysis, anomalies=anomalies, vehicle=vehicle,
                                             batch_ref="B1", selected_start=analysis["samples"][0]["timestamp"],
                                             selected_end=analysis["samples"][-1]["timestamp"])
    events = {event["evidence_id"]: event for event in payload["anomaly_events"]}
    catalog = evidence_detail_catalog(payload)
    facts, guide = st.columns([2, 1])
    with facts, st.container(border=True):
        st.subheader("核查 Python 计算的证据")
        evidence_ids = [*events, *(item for item in catalog if item not in events)]
        evidence_id = st.selectbox("虚构证据 ID", evidence_ids, key="ai_demo_evidence")
        rows = catalog[evidence_id]
        st.dataframe([{"指标": row["指标"], "值 / 单位": f"{row['值']} {row['单位']}".strip()}
                      for row in rows], hide_index=True, width="stretch")
        st.caption(f"计算口径：{rows[0]['口径']}")
        if evidence_id in events:
            event = events[evidence_id]
            event_replay_button(label="在三维回放中查看虚构区段", key="ai_demo_bogie_jump",
                                vehicle_id=vehicle_id, batch_id=batch, threshold=threshold,
                                timestamp=event["start_timestamp"], axis=event["type"], fictional=True)
    with guide, st.container(border=True):
        st.subheader("如何解释证据")
        st.write("证据 ID 对应聚合指标或异常区段，可以回查计算口径和样本。")
        st.caption("超过阈值描述的是数据状态。即使 AI 引用了正确证据，可能原因也需要进一步验证。")
        if WORKBENCH_RETURN in st.session_state:
            render_workbench_return("ai_demo_return_analysis")
        else:
            public = public_demo_enabled()
            st.button("返回分析工作台" if public else "返回演示中心", key="ai_demo_back", on_click=navigate,
                      args=("统计与异常演示" if public else "演示中心",))


def render():
    page_title("AI 分析中心", "核查计算证据 / 主动生成解读 / 回查本机记录")
    st.caption("AI 内容仅供辅助参考，不构成故障、事故、安全、法规或标准舒适等级结论。")
    public = public_demo_enabled()
    if public:
        st.info("公开演示模式仅使用内置虚构证据，不调用 AI 服务。")
        _render_demo_evidence()
        return
    if st.toggle("查看离线虚构证据演示", key="ai_center_demo"):
        _render_demo_evidence()
        return
    if st.toggle("查看本机 AI 历史", key="ai_center_show_history"):
        _render_local_history()
        return
    available = {kind: item for kind in CONTEXTS if (item := current_context(st.session_state, kind)) is not None}
    if not available:
        st.info("当前会话还没有可核查的分析结果。先选择车辆和运行批次，完成分析后再回来解读证据。")
        st.button("前往分析工作台", key="ai_center_open_analysis", type="primary", on_click=navigate,
                  args=("运行分析",), kwargs={ANALYSIS_MODE: "single"})
        _render_history()
        return
    left, middle, right = st.columns([1.0, 1.8, 1.8])
    with left:
        st.subheader("分析对象")
        requested = st.session_state.pop("ai_center_requested_kind", None)
        if requested in available:
            st.session_state["ai_center_kind"] = requested
        if st.session_state.get("ai_center_kind") not in available:
            st.session_state["ai_center_kind"] = next(iter(available))
        kind = st.selectbox("分析类型", list(available), format_func=lambda value: CONTEXTS[value][0], key="ai_center_kind")
        destination = {"single": "运行分析", "batch": "运行分析", "ml": "机器学习异常检测",
                       "vibration": "振动与平稳性分析", "fleet": "多车辆运行对比"}[kind]
        return_options = {ANALYSIS_MODE: "comparison" if kind == "batch" else "single",
                          WORKBENCH_SOURCE: "history"} if kind in {"single", "batch"} else None
        if kind == "single" and WORKBENCH_RETURN in st.session_state:
            st.button("返回分析页面", on_click=return_to_workbench)
        else:
            st.button("返回分析页面", on_click=navigate, args=(destination,), kwargs=return_options)
        item = available[kind]
        payload, selection = item["payload"], item["selection"]
        can_jump = kind == "single" and all(key in selection for key in ("vehicle_id", "batch_id", "threshold"))
        events = payload.get("anomaly_events", []) if can_jump else []
        key = CONTEXTS[kind][2]
        st.caption("只保存在当前会话；更改源页面的选择或参数后，需重新确认当前结果。")
        try:
            config = AIConfig.from_sources()
        except Exception:
            st.warning("AI 配置读取失败，请检查本地配置；离线分析和报告仍可使用。")
            config = AIConfig(enabled=False)
        st.caption(f"Provider：{config.provider}；密钥仅从本机配置读取，不在页面显示。")
        model_key = f"ai_center_model_{config.provider}"
        source_key = f"ai_center_config_model_{config.provider}"
        # Keep manual edits on rerun, but do not keep using a stale model after
        # the underlying configuration changes during the same session.
        if model_key not in st.session_state or (source_key in st.session_state and st.session_state[source_key] != config.model):
            st.session_state[model_key] = config.model
        st.session_state[source_key] = config.model
        model = st.text_input("模型名称", key=model_key)
        timeout = st.number_input("超时（秒）", min_value=1, max_value=300,
                                  value=int(config.timeout), key="ai_center_timeout")
        config = replace(config, model=model.strip(), timeout=float(timeout))
        unavailable = ai_configuration_message(config)
        if unavailable:
            st.warning(unavailable)
        else:
            st.success("AI 服务已配置；仅在主动点击后请求。")
        st.caption("Provider 和密钥通过本地配置管理；模型与超时调整仅作用于当前会话。")
    with middle:
        st.subheader("辅助解读")
        st.info("仅发送已计算的聚合指标与异常事件，不发送原始 CSV、样本、备注或 PDF。")
        failure_key = "ai_center_last_failure"
        failure_identity = (kind, item["input_fingerprint"], item["selection_fingerprint"], config.provider, config.model, config.timeout)
        previous_failure = st.session_state.get(failure_key)
        if unavailable or not isinstance(previous_failure, dict) or previous_failure.get("identity") != failure_identity:
            st.session_state.pop(failure_key, None)
        invalidate_stale_result(st.session_state, key, payload, selection)
        previous = st.session_state.get(key)
        metadata = previous.get("ai_metadata", {}) if isinstance(previous, dict) else {}
        if unavailable or (metadata and (metadata.get("provider") != config.provider or metadata.get("model") != config.model)):
            clear_result(st.session_state, key)
        if st.button("生成 AI 辅助解读", key="ai_center_generate", type="primary", disabled=bool(unavailable)):
            clear_result(st.session_state, key)
            st.session_state.pop(failure_key, None)
            with st.spinner("正在解读已计算结果……"):
                result = request_ai_analysis(payload, config, has_data=True, provider_factory=create_configured_provider)
            if result.get("status") == "available" and result.get("scope_validation") == "verified":
                result["selection_fingerprint"] = selection_fingerprint(selection)
                result["ai_metadata"] = dict(provider=config.provider, model=config.model,
                                             generated_at=datetime.now(timezone.utc).isoformat(), status="success")
                st.session_state[key] = result
                history = st.session_state.get("ai_center_history", ())
                st.session_state["ai_center_history"] = (
                    (CONTEXTS[kind][0], result["ai_metadata"].copy()), *history)[:10]
            else:
                message = status_message(result)
                if result.get("code") == "incomplete_response" and not isinstance(result.get("diagnostics"), dict):
                    message += "\n服务未提供安全诊断，请确认已刷新到最新页面后重试。"
                # Only retain a fixed, sanitized UI message, never the raw result.
                st.session_state[failure_key] = {"identity": failure_identity, "message": message}
        failure = st.session_state.get(failure_key)
        if isinstance(failure, dict):
            st.warning(failure["message"])
        saved = st.session_state.get(key)
        if saved is not None:
            if saved.get("status") != "available" or saved.get("scope_validation") != "verified":
                clear_result(st.session_state, key)
                st.warning("AI 结果尚未完整验证，未展示。")
            elif not render_validated_result(saved, payload, st):
                clear_result(st.session_state, key)
            else:
                if can_jump:
                    _cited_event_links(saved["analysis"], events, selection)
                _render_save_history(payload, selection, saved)
        else:
            st.caption("尚未生成 AI 解读；右侧可离线核查当前计算指标。")
    with right:
        st.subheader("数据与证据")
        st.caption("下列信息为 Python 已计算事实；证据范围匹配不证明自然语言推理正确。")
        with st.expander("分析范围（仅本地）", expanded=False):
            st.json(selection)
        try:
            details = evidence_detail_catalog(payload)
            st.caption(f"当前可引用 {len(details)} 个证据 ID。")
            if details:
                st.caption("离线结果说明：选择证据后查看 Python 已计算的值及口径，无需 API。")
                evidence_id = st.selectbox("核查证据", list(details), key=f"ai_center_evidence_{kind}")
                rows = details[evidence_id]
                st.dataframe([{"指标": row["指标"], "值 / 单位": f"{row['值']} {row['单位']}".strip()}
                              for row in rows], hide_index=True, width="stretch",
                             column_config={"指标": st.column_config.TextColumn("指标", width=110)})
                st.caption(f"类别：{rows[0]['类别']}；轴向：{rows[0]['轴向']}；批次：{rows[0]['批次范围']}")
                st.caption(f"计算口径：{rows[0]['口径']}")
                event = next((event for event in events if event["evidence_id"] == evidence_id), None)
                if event:
                    _event_button(event, selection, "ai_center_evidence_jump", "在三维回放中查看此区段")
                    st.caption("跳转使用原始统计证据的时间和阈值。")
            else:
                st.info("当前分析没有可展示的证据。")
        except (ValueError, TypeError, KeyError):
            st.warning("当前证据目录无法读取，请返回分析页面重新计算。")
    _render_history()
