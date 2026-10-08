"""Running-data import and basic dynamics analysis page."""

import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.run_data import (
    PREVIEW_LIMIT,
    import_run_data,
    list_batch_samples,
    list_import_batches,
    list_vehicle_batches,
    validate_run_csv,
)
from rail_vehicle.vehicle_data import list_vehicles


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
    threshold = st.number_input(
        "加速度绝对值阈值（m/s²）",
        min_value=0.0,
        value=1.0,
        step=0.1,
        help="同一阈值用于统计占比和异常识别；绝对值严格大于阈值时标为数据异常点。",
    )

    samples = list_batch_samples(vehicle_id, batch_id)
    try:
        result = analyze_run_data(samples, threshold)
    except ValueError as exc:
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


def render() -> None:
    st.header("运行分析")
    import_tab, analysis_tab = st.tabs(
        ["CSV 数据导入", "基础动力学统计分析"]
    )
    with import_tab:
        _render_import()
    with analysis_tab:
        _render_dynamics_analysis()
