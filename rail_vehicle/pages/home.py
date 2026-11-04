"""Overview: real database totals and explicitly scoped computed preview."""

import sqlite3
from zoneinfo import ZoneInfoNotFoundError
import streamlit as st

from rail_vehicle.dashboard import load_dashboard
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.ui import navigate, plot_chart, page_title, kpi, chart_container, empty_state, data_table, action_button, status_label, source_caption
from rail_vehicle.dashboard_chart import (dashboard_chart, dashboard_events, display_timestamp,
                                         DISPLAY_TIMEZONES, EVENTS_PER_PAGE, HOME_PLOT_CONFIG)


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
        page_title("平台首页", primary=True)
        source_caption(fictional=public)
    with controls:
        with st.popover("预览设置", width="stretch"):
            threshold = st.number_input("预览加速度阈值（m/s²）", min_value=0., value=1., step=.1, key="home_threshold")
            timezone_name = st.selectbox("时间显示", DISPLAY_TIMEZONES, key="home_timezone",
                                        format_func=lambda name: "北京时间（Asia/Shanghai）" if name == "Asia/Shanghai" else "UTC")
            try:
                display_timestamp("2000-01-01T00:00:00Z", timezone_name)
            except ZoneInfoNotFoundError:
                timezone_name = "UTC"
                st.warning("当前环境缺少北京时间时区数据，暂按 UTC 显示。")
            st.caption("仅影响最近车辆/批次预览；不改变原始数据。")
    if not public and data is not None and data["vehicle_count"] == 0:
        with st.container(border=True):
            st.subheader("首次使用：三步完成一次分析")
            st.markdown("1. 在 **车辆参数** 中登记车辆，或先体验演示中心的虚构数据。\n"
                        "2. 在 **CSV 数据导入** 中下载虚构示例并按提示登记对应车辆，上传并确认导入。\n"
                        "3. 在 **运行分析** 中选择车辆和批次，查看曲线、统计与 PDF 报告。")
            st.caption("基础统计、异常检测和 PDF 不需要 AI 或 API 密钥；演示数据不代表实际车辆。")
    preview = data.get("preview") if data else None
    analysis, anomalies = None, None
    invalid = False
    if preview and preview["samples"]:
        try:
            analysis = analyze_run_data(preview["samples"], threshold)
            anomalies = detect_anomalies(analysis["samples"], threshold)
        except (ValueError, KeyError, TypeError, OverflowError):
            invalid = True
    if analysis and timezone_name != "UTC":
        try:
            for row in (analysis["samples"][0], analysis["samples"][-1]):
                display_timestamp(row["timestamp"], timezone_name)
        except OverflowError:
            timezone_name = "UTC"
            st.warning("该批次时间超出北京时间可显示范围，暂按 UTC 显示。")
    columns = st.columns(4)
    for column, title, key, detail in zip(columns[:3], ("登记车辆", "导入批次", "采样点总数"),
            ("vehicle_count", "batch_count", "sample_count"), ("数据库车辆档案", "已成功导入", "数据库完整计数")):
        value = f"{data[key]:,}{' 点' if key == 'sample_count' else ''}" if data else "—"
        kpi(column, title, value, detail if data else "无可用数据库统计")
    kpi(columns[3], "统计异常区段", len(anomalies["events"]) if anomalies else "—", f"最近车辆/批次 · 阈值 |a| > {threshold:g} m/s²")
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
                st.caption(f"{preview['vehicle_code']}  /  批次 #{preview['batch_id']}  /  {preview['sample_count']:,} 个采样点  /  {timezone_name}")
                events = anomalies["events"]
                pages = max(1, (len(events) + EVENTS_PER_PAGE - 1) // EVENTS_PER_PAGE)
                event_page = st.selectbox("异常区段展示页", range(pages), format_func=lambda value: f"第 {value + 1} / {pages} 页", key="home_event_page") if pages > 1 else 0
                visible = dashboard_events(anomalies, event_page)
                st.html('<div class="rv-legend"><span><b>◆</b> 超阈值采样点</span>'
                        '<span><b>▰</b> 异常区段</span><span><b>│</b> 首末时间相同的区段</span>'
                        '<span>┄ 阈值线</span></div>')
                plot_chart(dashboard_chart(analysis, anomalies, event_page, timezone_name=timezone_name), key="home_speed", width="stretch", config=HOME_PLOT_CONFIG)
                st.subheader("异常区段列表")
                if visible:
                    if pages > 1:
                        st.caption(f"共 {len(events)} 段；图中编号与列表仅展示第 {visible[0][0]}–{visible[-1][0]} 段。")
                    data_table([{"编号": f"#{index}", "通道": "横向" if event["field"] == "lateral_accel" else "垂向",
                                 f"开始时间（{timezone_name}）": display_timestamp(event["start_timestamp"], timezone_name).isoformat(),
                                 f"结束时间（{timezone_name}）": display_timestamp(event["end_timestamp"], timezone_name).isoformat(),
                                 "峰值加速度（m/s²，带符号）": event["maximum_signed_value"]}
                                for index, event in visible])
                else:
                    st.info("当前阈值下没有统计异常区段。")
                with st.expander("数据范围与绘图说明"):
                    st.caption("仅此车辆/批次，不代表全平台异常总数。每通道最多绘制 1,200 个采样点；区段按页展示，统计使用整批预览数据。")
                    st.caption("时区切换不改变样本、指标或事件数量。空档不插值，也不证明空档内持续异常。")
            st.warning("⚠️ 数据异常仅用于分析参考，不构成车辆故障或运行安全结论。")
        with chart_container("rv-panel-imports"):
            st.subheader("最近导入记录")
            if data and data["imports"]:
                data_table([dict(批次=row["id"], 文件=row["file_name"], 样本数=row["row_count"], 导入时间=row["imported_at"]) for row in data["imports"]])
            else:
                st.caption("暂无导入记录。公开演示不读取历史导入数据。" if public else "暂无导入记录，请先导入 CSV。")
    with right:
        with chart_container("rv-panel-actions"):
            st.subheader("快捷操作")
            st.caption("从数据到结论，开始一次分析。")
            actions = [("统计与异常演示", "体验基础统计"), ("动态运行驾驶舱", "运行驾驶舱"), ("演示中心", "演示中心")] if public else [("运行分析", "开始分析"), ("CSV 数据导入", "导入数据"), ("车辆参数", "管理车辆"), ("演示中心", "虚构数据演示")]
            for target, title in actions:
                action_button(title+" →", key=f"home_{target}", on_click=navigate, args=(target,), width="stretch",
                              type="primary" if target in {"运行分析", "统计与异常演示"} else "secondary")
        if analysis and anomalies:
            with chart_container("rv-panel-summary"):
                st.subheader("当前批次摘要")
                st.metric("平均速度（km/h）", f"{analysis['speed']['mean']:.2f}")
                st.metric("统计异常点", len(anomalies["points"]))
                st.caption(f"加速度绝对值 > {threshold:g} m/s²；按通道计数。")
        with chart_container("rv-panel-recent"):
            st.subheader("最近分析")
            records = st.session_state.get("recent_analysis_records", [])
            if not records:
                st.markdown("**尚未进行分析**")
                st.caption("分析结果仅在当前会话中保留，刷新后清空。")
                action_button("去分析 →", key="home_recent_analysis", on_click=navigate,
                              args=("统计与异常演示" if public else "运行分析",), width="stretch")
            for row in records[:3]:
                st.markdown(f"**{row['分析类型']}**")
                st.caption(f"{row['数据点数']:,} 个样本 · {row['来源']}")
                st.caption(row["完成时间"])
            if len(records) > 3:
                with st.expander("更早的本会话记录"):
                    for row in records[3:]:
                        st.caption(f"{row['分析类型']} · {row['数据点数']:,} 样本 · {row['来源']} · {row['完成时间']}")
            if records:
                st.caption("仅记录本会话实际计算，刷新新会话后不保留。")
        status_label("公开虚构数据演示" if public else "本地分析工作空间")
