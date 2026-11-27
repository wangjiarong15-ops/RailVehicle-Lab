"""Overview: real database totals and explicitly scoped computed preview."""

import sqlite3
from zoneinfo import ZoneInfoNotFoundError
import streamlit as st

from rail_vehicle.dashboard import load_dashboard
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import EVENT_MERGE_NOTE, detect_anomalies_with_anchors
from rail_vehicle.analysis_history import SOURCES, source_kind, threshold_label, saved_scope
from rail_vehicle.pages.bogie_link import batch_replay_button, event_replay_button
from rail_vehicle.pages.data_workflow import WORKBENCH_SOURCE, analysis_button, import_button, open_analysis
from rail_vehicle.pages.demo_center import render_walkthrough
from rail_vehicle.ui import ANALYSIS, ANOMALIES, REPORT, ANALYSIS_MODE, navigate, plot_chart, page_title, kpi, chart_container, empty_state, data_table, source_caption
from rail_vehicle.dashboard_chart import (dashboard_chart, dashboard_events, display_timestamp, sample_label,
                                         DASHBOARD_POINT_LIMIT, DISPLAY_TIMEZONES, EVENTS_PER_PAGE, HOME_PLOT_CONFIG)


def render():
    public = public_demo_enabled()
    if public:
        page_title("演示路线", "统计 → 三维回放 → 证据核查", primary=True)
        source_caption(fictional=True)
        render_walkthrough()
        return
    data = None
    try:
        data = load_dashboard()
    except (sqlite3.Error, OSError):
        st.warning("暂时无法读取工作空间数据，请检查数据库后刷新。")
    heading, controls = st.columns([5, 1], vertical_alignment="center")
    with heading:
        page_title("工程工作台", primary=True)
        source_caption()
    with controls:
        with st.popover("预览设置", width="stretch"):
            threshold = st.number_input("预览加速度阈值（m/s²）", min_value=0., value=1., step=.1, key="home_threshold")
            threshold = 1. if threshold is None else threshold
            timezone_name = st.selectbox("时间显示", DISPLAY_TIMEZONES, key="home_timezone",
                                        format_func=lambda name: "北京时间（Asia/Shanghai）" if name == "Asia/Shanghai" else "UTC")
            try:
                display_timestamp("2000-01-01T00:00:00Z", timezone_name)
            except ZoneInfoNotFoundError:
                timezone_name = "UTC"
                st.warning("当前环境缺少北京时间时区数据，暂按 UTC 显示。")
            st.caption("仅影响最近车辆/批次预览；不改变原始数据。")
    preview = data.get("preview") if data else None
    analysis, anomalies = None, None
    invalid = False
    if preview and preview["samples"]:
        try:
            analysis = analyze_run_data(preview["samples"], threshold)
            anomalies, anchors = detect_anomalies_with_anchors(analysis["samples"], threshold, include_rows=True)
        except (ValueError, KeyError, TypeError, OverflowError):
            invalid = True
    if analysis and timezone_name != "UTC":
        try:
            for row in (analysis["samples"][0], analysis["samples"][-1]):
                display_timestamp(row["timestamp"], timezone_name)
        except OverflowError:
            timezone_name = "UTC"
            st.warning("该批次时间超出北京时间可显示范围，暂按 UTC 显示。")
    _render_next_step(data, preview, threshold)
    columns = st.columns(4)
    for column, title, key, detail in zip(columns[:3], ("登记车辆", "导入批次", "采样点总数"),
            ("vehicle_count", "batch_count", "sample_count"), ("数据库车辆档案", "已成功导入", "数据库完整计数")):
        value = f"{data[key]:,}{' 点' if key == 'sample_count' else ''}" if data else "—"
        kpi(column, title, value, detail if data else "无可用数据库统计")
    kpi(columns[3], "统计异常区段", len(anomalies["events"]) if anomalies else "—",
        f"最近车辆/批次 · {threshold_label(threshold)} · |a| > {threshold:g} m/s²")
    left, right = st.columns([3, 1])
    with left:
        with chart_container("rv-panel-trend"):
            st.subheader("运行趋势与异常区段")
            if not preview:
                empty_state("暂无可预览的运行数据。导入有效 CSV 后显示速度与双轴加速度。")
            elif not preview["samples"]:
                empty_state("最近批次超过首页 20,000 点预览上限，请进入运行分析查看完整数据。")
            elif invalid:
                st.warning("最近批次包含无法分析的记录，请在运行分析中检查数据。")
            else:
                kind = source_kind(preview.get("source_kind"), preview.get("engineering_json"))
                events = anomalies["events"]
                pages = max(1, (len(events) + EVENTS_PER_PAGE - 1) // EVENTS_PER_PAGE)
                event_page = st.selectbox("异常区段展示页", range(pages), format_func=lambda value: f"第 {value + 1} / {pages} 页", key="home_event_page") if pages > 1 else 0
                visible = dashboard_events(anomalies, event_page)
                source_column, event_column = st.columns([3, 2], vertical_alignment="bottom")
                with source_column:
                    st.caption(f"{preview['vehicle_code']} · 批次 #{preview['batch_id']} · {SOURCES[kind]} · "
                               f"{preview['sample_count']:,} 个采样点 · {timezone_name}")
                    st.html('<div class="rv-legend"><span><b>◆</b> 超阈值采样点</span>'
                            '<span><b>▰</b> 异常区段</span><span><b>│</b> 首末时间相同的区段</span>'
                            '<span>┄ 阈值线</span></div>')
                selected = event_column.selectbox("高亮区段", [None, *(index for index, _ in visible)],
                                                  format_func=lambda n: "请选择区段" if n is None else f"#{n}",
                                                  key=f"home_event_{preview['vehicle_id']}_{preview['batch_id']}_{threshold}_{event_page}") if visible else None
                plot_chart(dashboard_chart(analysis, anomalies, event_page, timezone_name=timezone_name,
                                           selected_event=selected), key="home_speed", width="stretch", config=HOME_PLOT_CONFIG)
                if selected is not None:
                    event = events[selected-1]
                    event_replay_button(label="在三维中定位该区段", key="home_event_replay", vehicle_id=preview["vehicle_id"],
                                        batch_id=preview["batch_id"], threshold=threshold, timestamp=event["start_timestamp"],
                                        axis="lateral" if event["field"] == "lateral_accel" else "vertical",
                                        anchor=anchors[selected-1])
                st.subheader("异常区段列表")
                if visible:
                    if pages > 1:
                        st.caption(f"共 {len(events)} 段；图中编号与列表仅展示第 {visible[0][0]}–{visible[-1][0]} 段。")
                    data_table([{"编号": f"#{index}", "通道": "横向" if event["field"] == "lateral_accel" else "垂向",
                                 f"开始时间（{timezone_name}）": display_timestamp(event["start_timestamp"], timezone_name).isoformat(),
                                 f"结束时间（{timezone_name}）": display_timestamp(event["end_timestamp"], timezone_name).isoformat(),
                                 "峰值加速度（m/s²，带符号）": event["maximum_signed_value"],
                                 "原始峰值记录": sample_label(analysis["samples"][event["peak_index"]], event["peak_index"])}
                                for index, event in visible])
                else:
                    st.info("当前阈值下没有统计异常区段。")
                with st.expander("数据范围与绘图说明"):
                    st.caption(f"预览使用整个车辆批次计算，绘图每通道最多 {DASHBOARD_POINT_LIMIT:,} 条，"
                               "分段保留极值和本页区段的首末及峰值记录。")
                    st.caption("时区切换不改变样本、指标或事件数量。空档不插值，也不证明空档内持续异常。")
                    st.caption(EVENT_MERGE_NOTE)
                    st.caption("预置和自定义阈值供分析使用。当前批次无已核验标准限值。")
            st.warning("⚠️ 数据异常仅用于分析参考，不构成车辆故障或运行安全结论。")
        with chart_container("rv-panel-imports"):
            st.subheader("最近导入记录")
            if data and data["imports"]:
                data_table([dict(批次=row["id"], 文件=row["file_name"], 样本数=row["row_count"], 导入时间=row["imported_at"]) for row in data["imports"]])
            else:
                st.caption("暂无导入记录，请先导入 CSV。")
    with right:
        with chart_container("rv-panel-demo"):
            st.subheader("虚构数据演练")
            st.caption("在波形上定位一次横向异常，再查看对应时刻的转向架。")
            st.button("体验异常回放", icon=":material/timeline:", on_click=navigate,
                      args=("联动分析",), kwargs={"bogie_source": "内置虚构演示"})
        with chart_container("rv-panel-recent"):
            st.subheader("最近分析")
            saved = data.get("recent", []) if data else []
            for row in saved[:5]:
                st.caption(f"{row['vehicle_code']} · 批次 #{row['batch_id']} · {SOURCES[source_kind(row['source_kind'])]} · "
                           f"{row['analyzed_at']} · |a| > {row['threshold']:g} m/s²")
                st.button("恢复分析范围", key=f"home_restore_{row['id']}", on_click=open_analysis,
                          args=(row["vehicle_id"], row["batch_id"]),
                          kwargs=dict(threshold=row["threshold"], scope=saved_scope(row), saved=row))
            records = [row for row in st.session_state.get("recent_analysis_records", [])
                       if not saved or row["分析类型"] != "单批次统计分析" or row["来源"] == "虚构演示"]
            if not records and not saved:
                st.markdown("**尚未进行分析**")
                st.caption("打开一次历史批次的运行分析后，可在此恢复它的范围与阈值。")
            for row in records[:3]:
                st.markdown(f"**{row['分析类型']}**")
                st.caption(f"{row['数据点数']:,} 个样本 · {row['来源']}")
                st.caption(row["完成时间"])
            if len(records) > 3:
                with st.expander("更早的本会话记录"):
                    for row in records[3:]:
                        st.caption(f"{row['分析类型']} · {row['数据点数']:,} 样本 · {row['来源']} · {row['完成时间']}")
            if records:
                st.caption("未生成保存项的演示、对比等记录随本次会话保留。")
        if preview and analysis and anomalies:
            with st.expander("批次统计详情"):
                st.subheader("当前批次摘要")
                st.caption(f"{preview['vehicle_code']} · 批次 #{preview['batch_id']}")
                st.metric("平均速度（km/h）", f"{analysis['speed']['mean']:.2f}")
                st.metric("统计异常点", len(anomalies["points"]))
                st.caption(f"加速度绝对值 > {threshold:g} m/s²；按通道计数。")



def _render_next_step(data, preview, threshold):
    if data is None:
        return
    with chart_container("rv-panel-actions"):
        st.subheader("继续最近批次" if preview else "开始工作")
        with st.container(horizontal=True, vertical_alignment="center"):
            if data["vehicle_count"] == 0:
                st.caption("登记车辆 → 导入 CSV → 查看运行分析。")
                st.button("登记车辆", key="home_车辆参数", type="primary", on_click=navigate, args=("车辆参数",))
            elif data["batch_count"] == 0:
                st.caption("已有车辆档案，导入一批运行样本即可开始分析。")
                import_button(st, label="导入 CSV", key="home_CSV 数据导入", primary=True)
            elif preview:
                st.caption(f"继续处理 {preview['vehicle_code']} · 批次 #{preview['batch_id']}")
                analysis_button(st, label="分析此批次", key="home_latest_analysis", primary=True,
                                vehicle_id=preview["vehicle_id"], batch_id=preview["batch_id"], threshold=threshold)
                analysis_button(st, label="查看异常", key="home_latest_events", page=ANOMALIES,
                                vehicle_id=preview["vehicle_id"], batch_id=preview["batch_id"], threshold=threshold)
                batch_replay_button(st, label="三维回放", key="home_latest_bogie",
                                    vehicle_id=preview["vehicle_id"], batch_id=preview["batch_id"], threshold=threshold)
                analysis_button(st, label="准备报告", key="home_latest_report", page=REPORT,
                                vehicle_id=preview["vehicle_id"], batch_id=preview["batch_id"], threshold=threshold)
            else:
                st.button("选择运行批次", type="primary", on_click=navigate, args=(ANALYSIS,),
                          kwargs={ANALYSIS_MODE: "single"})
            if data["batch_count"] == 0:
                st.button("打开内置虚构演示", key="home_try_demo", icon=":material/science:", on_click=navigate,
                          args=(ANALYSIS,), kwargs={WORKBENCH_SOURCE: "demo", ANALYSIS_MODE: "single"})
        if data["batch_count"] == 0:
            st.caption("暂时没有 CSV？可先用内置虚构数据体验异常回查、三维回放和报告。演示数据不会写入数据库。")
