"""Interactive sample drill-down of an already completed local ML experiment."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from rail_vehicle.ml_review import flagged_indices, sample_window


LABELS = dict(flagged="全部标记样本", both="两者均标记", ml_only="仅模型标记", rule_only="仅规则标记")
FIELDS = (("speed_kmh", "速度（km/h）"), ("lateral_accel", "横向加速度（m/s²）"),
          ("vertical_accel", "垂向加速度（m/s²）"))


def review_chart(window, threshold):
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=.08)
    visible = window["visible"]
    for index, (field, label) in enumerate(FIELDS, 1):
        figure.add_trace(go.Scatter(x=[row["relative_seconds"] for row in visible],
                                   y=[row[field] for row in visible], mode="markers", name=label,
                                   marker=dict(size=4, color="#2563eb")), row=index, col=1)
        figure.add_trace(go.Scatter(x=[0], y=[window["selected"][field]], mode="markers",
                                   name="所选样本", showlegend=index == 1,
                                   marker=dict(size=11, color="#dc2626", symbol="diamond")), row=index, col=1)
        if index > 1:
            for level in (-threshold, threshold):
                figure.add_hline(y=level, line_dash="dash", line_color="#d97706", row=index, col=1)
        figure.update_yaxes(title_text=label, row=index, col=1)
    figure.update_xaxes(title_text="相对所选样本时间（秒）", row=3, col=1)
    figure.update_layout(height=620, margin=dict(l=20, r=20, t=25, b=20), legend=dict(orientation="h", y=1.08))
    return figure


def render_review(result, fingerprint):
    with st.expander("标记样本回查 · 查看前后运行变化", expanded=False):
        if st.session_state.get("ml_review_fingerprint") != fingerprint:
            st.session_state.pop("ml_review_sample", None)
            st.session_state["ml_review_fingerprint"] = fingerprint
        category = st.selectbox("回查分类", list(LABELS), format_func=LABELS.get, key="ml_review_category")
        indices = flagged_indices(result["records"], category)
        if not indices:
            st.info("当前分类没有标记样本，请选择其他分类；模型未标记不代表安全。")
            st.session_state.pop("ml_review_sample", None)
            return
        if st.session_state.get("ml_review_sample") not in indices:
            st.session_state.pop("ml_review_sample", None)
        selected = st.selectbox("选择回查样本", indices,
            format_func=lambda i: f"第 {i+1} 条 · {result['records'][i]['timestamp']} · {LABELS[result['records'][i]['category']]}",
            key="ml_review_sample")
        seconds = st.slider("前后窗口（秒，含边界）", 0, 60, 5, key="ml_review_seconds")
        window = sample_window(result["records"], selected, seconds)
        sample = window["selected"]
        st.table([dict(样本序号=selected+1, 速度_kmh=sample["speed_kmh"],
                       横向加速度_ms2=sample["lateral_accel"], 垂向加速度_ms2=sample["vertical_accel"],
                       模型离群分数_非概率=sample["ml_score"])])
        st.caption(f"UTC：{sample['timestamp']}；分类：{LABELS[sample['category']]}；模型阈值：{result['score_threshold']:.6g}；加速度规则阈值：{result['rule_threshold']:.6g} m/s²。")
        st.caption("样本序号为当前检测结果按 UTC 排序后的记录顺序，不是 CSV 原始行号；内置实验使用虚构数据，历史批次使用用户导入数据。")
        st.plotly_chart(review_chart(window, result["rule_threshold"]), width="stretch", key="ml_review_chart")
        st.caption(f"窗口含 {len(window['rows'])} 条原有记录，绘制 {len(window['visible'])} 条；红色菱形为所选样本，橙色虚线为 ±规则阈值。大窗口最多等距抽取约 2,000 条并保留所选样本，不能据此排除未绘制的局部变化。")
        st.caption("按 UTC 时间差回查，重复时间戳按不同记录保留；散点不插值，不将采样空档解释成连续观测。窗口不重新训练、不改变分类，不证明异常原因或车辆故障；不发送给 AI，也不改变当前报告。")
