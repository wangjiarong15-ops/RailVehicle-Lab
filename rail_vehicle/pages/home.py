"""Overview: real database totals and explicitly scoped computed preview."""

import sqlite3
from datetime import timezone
import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.dashboard import load_dashboard
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.ui import navigate, plot_chart
from rail_vehicle.timestamp_utils import parse_iso_timestamp


def _utc(value):
    timestamp = parse_iso_timestamp(value)
    return timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)


def render():
    st.header("平台首页")
    st.caption("OVERVIEW / 运行数据资产 · 分析工作流 · 最近动态")
    public = public_demo_enabled()
    if public:
        data = None
        st.info("公开演示工作空间：不连接本地数据库。资产总量不适用，可从演示中心使用虚构数据。")
    else:
        try:
            data = load_dashboard()
        except (sqlite3.Error, OSError):
            data = None
            st.warning("暂时无法读取工作空间数据，请检查数据库后刷新。")
    columns = st.columns(3)
    for column, title, key, icon in zip(columns, ("登记车辆", "导入批次", "运行样本"),
                                       ("vehicle_count", "batch_count", "sample_count"), ("🚆", "▤", "∿")):
        column.metric(title, f"{data[key]:,}" if data else "—", border=True)
    st.subheader("开始一项工作")
    actions = [("统计与异常演示", "体验基础统计", "虚构数据 · 无需导入"),
               ("动态运行驾驶舱", "打开运行驾驶舱", "历史回放 · 速度与双轴加速度"),
               ("演示中心", "进入演示中心", "五条可操作的演示流程")] if public else [
               ("车辆参数", "管理车辆", "基础参数与车辆档案"),
               ("CSV 数据导入", "导入运行数据", "校验 CSV · 事务性保存"),
               ("运行分析", "开始运行分析", "统计 · 异常 · PDF 与 AI 解读")]
    for column, (target, title, caption) in zip(st.columns(3), actions):
        with column.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(caption)
            st.button("进入工作台 →", key=f"home_{target}", on_click=navigate, args=(target,), width="stretch")
    left, right = st.columns([2, 1])
    with left.container(border=True):
        st.subheader("最近批次运行趋势")
        preview = data.get("preview") if data else None
        if not preview:
            st.info("暂无可预览的运行数据。导入有效 CSV 后，这里会展示最近批次趋势。")
        elif not preview["samples"]:
            st.info("最近批次超过首页 20,000 点预览上限，请进入运行分析查看完整数据。")
        else:
            threshold = st.number_input("预览加速度阈值（m/s²）", min_value=0., value=1., step=.1, key="home_threshold")
            st.caption(f"车辆 {preview['vehicle_code']} · 批次 #{preview['batch_id']} · 全批次 {preview['sample_count']:,} 条；仅此车辆与批次，不代表全平台异常总数。")
            try:
                analysis = analyze_run_data(preview["samples"], threshold)
                anomalies = detect_anomalies(analysis["samples"], threshold)
            except (ValueError, KeyError, TypeError, OverflowError):
                st.warning("最近批次包含无法分析的记录，请在运行分析中检查数据。")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("平均速度（km/h）", f"{analysis['speed']['mean']:.2f}")
                c2.metric("统计异常点", len(anomalies["points"]))
                c3.metric("统计异常区段", len(anomalies["events"]))
                samples = analysis["samples"]
                # Display at most 1200 chronological points; statistics use all rows.
                indices = sorted({round(i * (len(samples)-1) / min(1199, max(1, len(samples)-1))) for i in range(min(1200, len(samples)))})
                figure = go.Figure(go.Scatter(x=[_utc(samples[i]["timestamp"]) for i in indices],
                                             y=[samples[i]["speed_kmh"] for i in indices], mode="markers", name="速度"))
                figure.update_layout(height=300, xaxis_title="时间（UTC）", yaxis_title="速度（km/h）", margin=dict(l=20,r=20,t=20,b=20))
                plot_chart(figure, key="home_speed", width="stretch")
                st.caption("均值按样本等权；异常规则为 |加速度| > 阈值。曲线最多 1,200 点，不插值；统计异常不构成故障或安全结论。")
    with right.container(border=True):
        st.subheader("最近分析记录")
        records = st.session_state.get("recent_analysis_records", [])
        if records:
            for row in records[:3]:
                st.markdown(f"**{row['分析类型']}**")
                st.caption(f"{row['数据点数']:,} 个样本 · {row['来源']}")
                st.caption(row["完成时间"])
            if len(records) > 3:
                with st.expander("更早的本会话记录"):
                    for row in records[3:]:
                        st.caption(f"{row['分析类型']} · {row['数据点数']:,} 样本 · {row['来源']} · {row['完成时间']}")
        else:
            st.info("本次会话尚未完成分析。")
        st.caption("仅记录本次会话实际执行的分析；刷新新会话后不保留，不代表持久化历史。")
    with st.container(border=True):
        st.subheader("最近导入记录")
        if data and data["imports"]:
            st.dataframe([dict(批次=row["id"], 文件=row["file_name"], 样本数=row["row_count"], 导入时间=row["imported_at"]) for row in data["imports"]], hide_index=True, width="stretch")
        else:
            st.caption("暂无导入记录。公开演示不读取历史导入数据。" if public else "暂无导入记录，请从“导入运行数据”开始。")
