"""Running-data import and basic dynamics analysis page."""

from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.analysis_report import (
    build_analysis_report_data,
    build_batch_comparison_report_data,
    generate_batch_comparison_report_pdf,
    generate_analysis_report_pdf,
)
from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.context import (
    build_batch_comparison_payload,
    build_single_batch_payload,
)
from rail_vehicle.ai.openai_provider import create_configured_provider
from rail_vehicle.ai.ui import (
    ai_configuration_message,
    clear_result,
    invalidate_stale_result,
    render_validated_result,
    request_ai_analysis,
    selection_fingerprint,
    status_message,
)
from rail_vehicle.ai.provider import input_fingerprint
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.time_filter import filter_samples_by_time_range
from rail_vehicle.run_data import (
    PREVIEW_LIMIT,
    import_run_data,
    list_batch_samples,
    list_import_batches,
    list_vehicle_batches,
    validate_run_csv,
)
from rail_vehicle.vehicle_data import get_vehicle, list_vehicles


def _render_ai_interpretation(
    *, payload: dict, selection: dict, state_key: str, has_data: bool = True
) -> None:
    st.subheader("🤖 AI 辅助解读")
    st.caption(
        "AI 生成内容，仅基于当前统计分析结果，不构成故障、事故、安全或法规结论。"
    )
    st.info(
        "AI 将仅使用当前页面已经计算出的统计指标、异常事件和批次对比结果进行解读，"
        "不会直接读取原始 CSV。"
    )
    if not has_data:
        clear_result(st.session_state, state_key)
        st.info("当前分析没有数据，无法生成 AI 解读。")
        return

    invalidate_stale_result(st.session_state, state_key, payload, selection)
    config = AIConfig.from_sources()
    unavailable_reason = ai_configuration_message(config)
    if unavailable_reason:
        clear_result(st.session_state, state_key)
        st.warning(unavailable_reason)

    generate = st.button(
        "🤖 生成 AI 辅助解读",
        key=f"generate_{state_key}",
        disabled=unavailable_reason is not None,
    )
    if generate:
        clear_result(st.session_state, state_key)
        result = request_ai_analysis(
            payload,
            config,
            has_data=True,
            provider_factory=create_configured_provider,
        )
        if result.get("status") == "available":
            result["selection_fingerprint"] = selection_fingerprint(selection)
            result["ai_metadata"] = {
                "provider": config.provider,
                "model": config.model,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "success",
            }
            st.session_state[state_key] = result
        else:
            st.warning(status_message(result))

    saved_result = st.session_state.get(state_key)
    if saved_result is not None:
        render_validated_result(saved_result, payload, st)


def _render_import() -> None:
    st.caption("上传 CSV 后先检查数据；只有整份文件通过校验后才能写入数据库。")

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

    vehicles = list_vehicles()
    if not vehicles:
        st.warning("请先在“车辆参数”页面新增车辆，再导入对应的运行数据。")

    uploaded_file = st.file_uploader("选择运行数据 CSV 文件", type=["csv"])
    if uploaded_file is not None:
        validation = validate_run_csv(uploaded_file.getvalue(), vehicles)
        if validation.preview:
            st.subheader("数据预览")
            st.dataframe(
                validation.preview,
                hide_index=True,
                use_container_width=True,
            )
            if validation.source_row_count > PREVIEW_LIMIT:
                st.caption(
                    f"显示前 {PREVIEW_LIMIT} 行，共 {validation.source_row_count} 行。"
                )

        if validation.errors:
            st.error("CSV 校验未通过，本文件不会写入数据库。")
            for error in validation.errors:
                st.write(f"- {error}")
        else:
            distinct_vehicles = len({row["vehicle_id"] for row in validation.rows})
            timestamps = [row["timestamp"] for row in validation.rows]
            metric_columns = st.columns(3)
            metric_columns[0].metric("数据行数", f"{len(validation.rows):,}")
            metric_columns[1].metric("涉及车辆", distinct_vehicles)
            metric_columns[2].metric(
                "时间范围", f"{min(timestamps)} — {max(timestamps)}"
            )
            st.success("格式检查通过，可以导入。")
            if st.button("导入到 SQLite", type="primary"):
                try:
                    batch_id = import_run_data(uploaded_file.name, validation)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success(
                        f"导入成功：批次 #{batch_id}，共保存 {len(validation.rows):,} 行。"
                    )

    st.divider()
    st.subheader("历史导入记录")
    batches = list_import_batches()
    if not batches:
        st.info("还没有导入记录。")
    else:
        st.dataframe(
            [
                {
                    "批次编号": batch["id"],
                    "文件名": batch["file_name"],
                    "数据行数": batch["row_count"],
                    "导入时间": batch["imported_at"],
                }
                for batch in batches
            ],
            hide_index=True,
            use_container_width=True,
        )


def _format_stats_table(metrics: dict, acceleration: bool = False) -> list[dict]:
    rows = [
        {"统计量": "最大值", "结果": metrics["maximum"]},
        {"统计量": "最小值", "结果": metrics["minimum"]},
        {"统计量": "平均值（算术平均）", "结果": metrics["mean"]},
    ]
    if acceleration:
        rows.append({"统计量": "RMS（均方根）", "结果": metrics["rms"]})
    return rows


def _render_dynamics_analysis() -> None:
    st.caption(
        "基础动力学统计分析 · 描述性统计结果不代表法规合规或安全结论。"
    )
    vehicles = list_vehicles()
    if not vehicles:
        clear_result(st.session_state, "ai_single_batch_result")
        st.info("请先录入车辆并导入运行数据。")
        return

    vehicle_id = st.selectbox(
        "选择车辆",
        options=[vehicle["id"] for vehicle in vehicles],
        format_func=lambda selected_id: next(
            f"{vehicle['vehicle_code']} · {vehicle['vehicle_type']}"
            for vehicle in vehicles
            if vehicle["id"] == selected_id
        ),
        key="dynamics_vehicle_id",
    )
    batches = list_vehicle_batches(vehicle_id)
    if not batches:
        clear_result(st.session_state, "ai_single_batch_result")
        st.info("该车辆还没有导入运行数据。")
        return

    batch_id = st.selectbox(
        "选择历史导入批次",
        options=[batch["id"] for batch in batches],
        format_func=lambda selected_id: next(
            f"{batch['file_name']} · {batch['imported_at']} · "
            f"{batch['sample_count']:,} 行"
            for batch in batches
            if batch["id"] == selected_id
        ),
        key="dynamics_batch_id",
    )
    samples = list_batch_samples(vehicle_id, batch_id)
    if not samples:
        clear_result(st.session_state, "ai_single_batch_result")
        st.info("所选批次没有可分析的运行数据。")
        return

    # Stored timestamps without an explicit offset have always been treated as UTC.
    # Present range controls in UTC too, so the selected bounds match analysis order.
    def as_utc(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    try:
        timestamps = [as_utc(sample["timestamp"]) for sample in samples]
    except (KeyError, TypeError, ValueError):
        clear_result(st.session_state, "ai_single_batch_result")
        st.error("所选批次包含无效时间戳，无法进行时间筛选。")
        return
    # The time controls have one-second precision. Floor the defaults to a
    # second and include that full second at the end so fractional timestamps
    # at either batch boundary remain part of the default complete range.
    batch_start = min(timestamps).replace(tzinfo=None, microsecond=0)
    batch_end = max(timestamps).replace(tzinfo=None, microsecond=0)
    st.markdown("**分析时间范围（UTC）**")
    start_col, end_col = st.columns(2)
    with start_col:
        start_date = st.date_input(
            "开始日期",
            value=batch_start.date(),
            key=f"analysis_start_date_{vehicle_id}_{batch_id}",
        )
        start_time = st.time_input(
            "开始时间",
            value=batch_start.time(),
            step=timedelta(seconds=1),
            key=f"analysis_start_time_{vehicle_id}_{batch_id}",
        )
    with end_col:
        end_date = st.date_input(
            "结束日期",
            value=batch_end.date(),
            key=f"analysis_end_date_{vehicle_id}_{batch_id}",
        )
        end_time = st.time_input(
            "结束时间",
            value=batch_end.time(),
            step=timedelta(seconds=1),
            key=f"analysis_end_time_{vehicle_id}_{batch_id}",
        )

    selected_start = datetime.combine(start_date, start_time).replace(
        tzinfo=timezone.utc
    )
    selected_end = (
        datetime.combine(end_date, end_time).replace(tzinfo=timezone.utc)
        + timedelta(seconds=1, microseconds=-1)
    )
    if selected_start > selected_end:
        clear_result(st.session_state, "ai_single_batch_result")
        st.warning("开始时间不能晚于结束时间，请调整筛选范围。")
        return
    try:
        filtered_samples = filter_samples_by_time_range(
            samples, selected_start, selected_end
        )
    except ValueError as exc:
        clear_result(st.session_state, "ai_single_batch_result")
        st.error(str(exc))
        return
    if not filtered_samples:
        clear_result(st.session_state, "ai_single_batch_result")
        st.info("所选时间范围内没有运行数据，请调整开始或结束时间。")
        return

    threshold = st.number_input(
        "加速度绝对值阈值（m/s²）",
        min_value=0.0,
        value=1.0,
        step=0.1,
        help="同一阈值用于统计占比和异常识别；绝对值严格大于阈值时标为数据异常点。",
    )

    try:
        result = analyze_run_data(filtered_samples, threshold)
    except ValueError as exc:
        clear_result(st.session_state, "ai_single_batch_result")
        st.error(str(exc))
        return
    anomaly_result = detect_anomalies(result["samples"], threshold)

    st.markdown(f"**分析样本数：** {result['sample_count']:,} 条")
    st.markdown(
        "**计算口径：** 最大值/最小值为样本极值；平均值为算术平均；"
        "RMS = √(Σx² / n)。加速度峰值取绝对值最大点，同时显示该点的有符号原始值和时间。"
    )

    st.subheader("速度统计")
    speed_columns = st.columns(3)
    speed_columns[0].metric("最大速度", f"{result['speed']['maximum']:.3f} km/h")
    speed_columns[1].metric("最小速度", f"{result['speed']['minimum']:.3f} km/h")
    speed_columns[2].metric("平均速度", f"{result['speed']['mean']:.3f} km/h")

    st.subheader("加速度统计")
    lateral_col, vertical_col = st.columns(2)
    with lateral_col:
        st.markdown("**横向加速度（m/s²）**")
        st.dataframe(
            _format_stats_table(result["lateral_accel"], acceleration=True),
            hide_index=True,
            use_container_width=True,
        )
    with vertical_col:
        st.markdown("**垂向加速度（m/s²）**")
        st.dataframe(
            _format_stats_table(result["vertical_accel"], acceleration=True),
            hide_index=True,
            use_container_width=True,
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
        use_container_width=True,
    )

    st.subheader("数据异常检测")
    st.caption(
        f"规则：横向或垂向加速度绝对值严格大于 {threshold:.3f} m/s² 时标记为统计异常点。"
        "同一方向相邻异常样本合并为一个区段，遇到正常样本则分段。"
        "异常区段仅描述数据/统计异常，不用于判断车辆故障或安全事故。"
    )
    anomaly_columns = st.columns(2)
    anomaly_columns[0].metric("数据异常点", len(anomaly_result["points"]))
    anomaly_columns[1].metric("统计异常区段", len(anomaly_result["events"]))
    st.markdown("**异常事件列表**")
    if anomaly_result["events"]:
        st.dataframe(
            [
                {
                    "异常类型": event["type"],
                    "开始时间": event["start_timestamp"],
                    "结束时间": event["end_timestamp"],
                    "持续时间（秒）": f"{event['duration_seconds']:.3f}",
                    "区段最大绝对值（m/s²）": f"{event['maximum_absolute_value']:.4f}",
                    "对应有符号值（m/s²）": f"{event['maximum_signed_value']:.4f}",
                    "峰值时间": event["maximum_timestamp"],
                    "异常样本数": event["sample_count"],
                }
                for event in anomaly_result["events"]
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("当前批次未发现超过所设阈值的数据异常点。")

    st.subheader("运行曲线")
    chart_specs = (
        ("speed_kmh", "速度—时间", "速度（km/h）"),
        ("lateral_accel", "横向加速度—时间", "横向加速度（m/s²）"),
        ("vertical_accel", "垂向加速度—时间", "垂向加速度（m/s²）"),
    )
    ordered_samples = result["samples"]
    for field, title, y_title in chart_specs:
        figure = go.Figure(
            data=[
                go.Scatter(
                    x=[sample["timestamp"] for sample in ordered_samples],
                    y=[sample[field] for sample in ordered_samples],
                    mode="lines",
                    name=y_title,
                    hovertemplate="%{x}<br>%{y:.4f}<extra></extra>",
                )
            ]
        )
        figure.update_layout(
            title=title,
            xaxis_title="时间",
            yaxis_title=y_title,
            margin={"l": 20, "r": 20, "t": 50, "b": 20},
        )
        if field in ("lateral_accel", "vertical_accel"):
            axis_points = [
                point
                for point in anomaly_result["points"]
                if point["field"] == field
            ]
            if axis_points:
                figure.add_trace(
                    go.Scatter(
                        x=[point["timestamp"] for point in axis_points],
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
        st.plotly_chart(figure, use_container_width=True, key=f"chart_{field}")

    selected_vehicle = next(
        vehicle for vehicle in vehicles if vehicle["id"] == vehicle_id
    )
    ai_payload = build_single_batch_payload(
        analysis=result,
        anomalies=anomaly_result,
        vehicle=selected_vehicle,
        batch_ref="B1",
        selected_start=selected_start.isoformat(),
        selected_end=selected_end.isoformat(),
    )
    ai_selection = {
        "vehicle_id": vehicle_id,
        "batch_id": batch_id,
        "selected_start": selected_start.isoformat(),
        "selected_end": selected_end.isoformat(),
        "threshold": threshold,
    }
    _render_ai_interpretation(
        payload=ai_payload,
        selection=ai_selection,
        state_key="ai_single_batch_result",
    )

    st.subheader("导出分析报告")
    st.caption("PDF 报告将包含当前车辆、批次、时间范围、阈值、统计结果、异常事件和筛选后曲线。")
    current_ai_result = st.session_state.get("ai_single_batch_result")
    current_ai_pdf_fingerprint = (
        current_ai_result.get("input_fingerprint", "")
        if isinstance(current_ai_result, dict)
        and current_ai_result.get("status") == "available"
        else "no-ai-result"
    )
    report_key = (
        f"{vehicle_id}:{batch_id}:{selected_start.isoformat()}:{selected_end.isoformat()}:"
        f"{threshold:.12g}:{current_ai_pdf_fingerprint}"
    )
    if st.session_state.get("analysis_report_key") != report_key:
        st.session_state.pop("analysis_report_key", None)
        st.session_state.pop("analysis_report_pdf", None)

    if st.button("生成 PDF 分析报告", type="primary", key="generate_analysis_report"):
        try:
            selected_vehicle = get_vehicle(vehicle_id)
            if selected_vehicle is None:
                raise ValueError("找不到所选车辆，请刷新页面后重试。")
            selected_batch = next(
                batch for batch in batches if batch["id"] == batch_id
            )
            report_data = build_analysis_report_data(
                vehicle=selected_vehicle,
                batch=selected_batch,
                analysis=result,
                anomalies=anomaly_result,
                selected_start=selected_start,
                selected_end=selected_end,
                ai_context=ai_payload,
                ai_result=st.session_state.get("ai_single_batch_result"),
                ai_selection_fingerprint=selection_fingerprint(ai_selection),
            )
            pdf_bytes = generate_analysis_report_pdf(report_data)
        except Exception as exc:
            st.error(f"分析报告生成失败：{exc}")
        else:
            st.session_state["analysis_report_key"] = report_key
            st.session_state["analysis_report_pdf"] = pdf_bytes
            st.success("分析报告已生成，可以下载 PDF 文件。")

    if (
        st.session_state.get("analysis_report_key") == report_key
        and st.session_state.get("analysis_report_pdf")
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
            data=st.session_state["analysis_report_pdf"],
            file_name=f"rail_vehicle_report_{safe_vehicle_code}_batch_{batch_id}.pdf",
            mime="application/pdf",
            key="download_analysis_report",
        )


def _render_batch_comparison() -> None:
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
        use_container_width=True,
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
        st.plotly_chart(figure, use_container_width=True, key=f"comparison_{field}")

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
        current_ai_result.get("input_fingerprint", "")
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
            st.error(f"批次对比报告生成失败：{exc}")
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


def render() -> None:
    st.header("运行分析")
    import_tab, analysis_tab, comparison_tab = st.tabs(
        ["CSV 数据导入", "基础动力学统计分析", "批次对比"]
    )
    with import_tab:
        _render_import()
    with analysis_tab:
        _render_dynamics_analysis()
    with comparison_tab:
        _render_batch_comparison()
