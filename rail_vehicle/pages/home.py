"""Overview: real database totals and explicitly scoped computed preview."""

import sqlite3
from datetime import timezone
import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.dashboard import load_dashboard
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.ui import navigate, plot_chart, page_title, kpi, chart_container, empty_state, data_table, action_button, status_label
from rail_vehicle.dashboard_chart import dashboard_chart
from rail_vehicle.timestamp_utils import parse_iso_timestamp


def _utc(value):
    timestamp = parse_iso_timestamp(value)
    return timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)


def render():
    public = public_demo_enabled()
    data = None
    if not public:
        try:
            data = load_dashboard()
        except (sqlite3.Error, OSError):
            st.warning("暂时无法读取工作空间数据，请检查数据库后刷新。")
    heading, controls = st.columns([4, 1], vertical_alignment="center")
    with heading:
        page_title("平台首页", "RAILVEHICLE LAB / 运行数据总览", primary=True)
    with controls:
        with st.popover("预览设置", width="stretch"):
            threshold = st.number_input("预览加速度阈值（m/s²）", min_value=0., value=1., step=.1, key="home_threshold")
            st.caption("仅影响最近车辆/批次预览；不改变原始数据。")
    preview = data.get("preview") if data else None
    analysis, anomalies = None, None
    invalid = False
    if preview and preview["samples"]:
        try:
            analysis = analyze_run_data(preview["samples"], threshold)
            anomalies = detect_anomalies(analysis["samples"], threshold)
        except (ValueError, KeyError, TypeError, OverflowError):
            invalid = True
    columns = st.columns(4)
    for column, title, key, detail in zip(columns[:3], ("登记车辆", "导入批次", "运行样本"),
            ("vehicle_count", "batch_count", "sample_count"), ("数据库车辆档案", "已成功导入", "数据库完整计数")):
        kpi(column, title, f"{data[key]:,}" if data else "—", detail if data else "无可用数据库统计")
    kpi(columns[3], "统计异常区段", len(anomalies["events"]) if anomalies else "—", "最近车辆/批次 · 当前阈值")
    left, right = st.columns([3, 1])
    with left:
        with chart_container("rv-panel-trend"):
            st.subheader("运行趋势与异常区段")
            if public:
                empty_state("公开演示工作空间：不连接本地数据库。请从演示中心使用虚构数据。")
            elif not preview:
                empty_state("暂无可预览的运行数据。导入有效 CSV 后显示速度与双轴加速度。")
            elif not preview["samples"]:
                empty_state("最近批次超过首页 20,000 点预览上限，请进入运行分析查看完整数据。")
            elif invalid:
                st.warning("最近批次包含无法分析的记录，请在运行分析中检查数据。")
            else:
                st.caption(f"{preview['vehicle_code']} / 批次 #{preview['batch_id']} · {preview['sample_count']:,} 条 · |a| > {threshold:g} m/s²；仅此车辆/批次，不代表全平台异常总数。")
                plot_chart(dashboard_chart(analysis, anomalies), key="home_speed", width="stretch")
                a, b = st.columns(2)
                a.metric("平均速度（km/h）", f"{analysis['speed']['mean']:.2f}")
                b.metric("统计异常点", len(anomalies["points"]))
                st.caption("每个通道最多绘制 1,200 点、最近 30 个区段；统计使用整批预览数据。红色为既有异常点/区段跨度，空档不插值，也不证明空档内持续异常。")
        with chart_container("rv-panel-imports"):
            st.subheader("最近导入记录")
            if data and data["imports"]:
                data_table([dict(批次=row["id"], 文件=row["file_name"], 样本数=row["row_count"], 导入时间=row["imported_at"]) for row in data["imports"]])
            else:
                st.caption("暂无导入记录。公开演示不读取历史导入数据。" if public else "暂无导入记录，请先导入 CSV。")
    with right:
        with chart_container("rv-panel-recent"):
            st.subheader("最近分析")
            records = st.session_state.get("recent_analysis_records", [])
            if not records:
                st.caption("本次会话尚未完成分析。")
            for row in records[:3]:
                st.markdown(f"**{row['分析类型']}**")
                st.caption(f"{row['数据点数']:,} 个样本 · {row['来源']}")
                st.caption(row["完成时间"])
            if len(records) > 3:
                with st.expander("更早的本会话记录"):
                    for row in records[3:]:
                        st.caption(f"{row['分析类型']} · {row['数据点数']:,} 样本 · {row['来源']} · {row['完成时间']}")
            st.caption("仅记录本会话实际计算，刷新新会话后不保留。")
        with chart_container("rv-panel-actions"):
            st.subheader("快捷操作")
            actions = [("统计与异常演示", "体验基础统计"), ("动态运行驾驶舱", "运行驾驶舱"), ("演示中心", "演示中心")] if public else [("车辆参数", "管理车辆"), ("CSV 数据导入", "导入数据"), ("运行分析", "开始分析")]
            for target, title in actions:
                action_button(title+" →", key=f"home_{target}", on_click=navigate, args=(target,), width="stretch")
        status_label("公开虚构数据演示" if public else "本地分析工作空间")
        st.caption("数据异常仅用于分析参考，不构成车辆故障或运行安全结论。")
