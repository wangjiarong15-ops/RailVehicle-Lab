"""Interactive local inspection of existing threshold events."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from rail_vehicle.ui import plot_chart

from rail_vehicle.ai.ui import selection_fingerprint
from rail_vehicle.anomaly import EVENT_DURATION_NOTE
from rail_vehicle.dashboard_chart import sample_label, share_time_hover
from rail_vehicle.anomaly_review import event_window
from rail_vehicle.pages.bogie_link import event_replay_button
from rail_vehicle.workspace_resume import EVENT, prior_event


def event_chart(window, threshold):
    event = window["event"]
    rows = {row["source_index"]: row for row in window["rows"]}
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True,
                           subplot_titles=("速度（km/h）", f"{event['type']}对应通道（m/s²）"))
    for row, field in ((1, "speed_kmh"), (2, event["field"])):
        figure.add_trace(go.Scatter(
            x=[sample["relative_seconds"] for sample in window["visible"]],
            y=[sample[field] for sample in window["visible"]],
            text=[sample_label(sample, sample["source_index"], window.get("first_row", 0))
                  for sample in window["visible"]],
            hovertemplate="%{y:.4f}<br>%{text}<extra></extra>",
            mode="markers", name="窗口样本" if row == 1 else "加速度样本",
            marker=dict(color="#2563eb", size=6)), row=row, col=1)
        for boundary in (0, window["duration"]):
            figure.add_vline(x=boundary, line_dash="dot", line_color="#7c3aed", row=row, col=1)
    figure.add_trace(go.Scatter(
        x=[point["relative_seconds"] for point in window["visible_points"]],
        y=[point["value"] for point in window["visible_points"]],
        text=[sample_label(rows[p["sample_index"]], p["sample_index"], window.get("first_row", 0))
              for p in window["visible_points"] if p.get("sample_index") in rows] or None,
        hovertemplate="%{y:.4f} m/s²<br>%{text}<extra></extra>",
        mode="markers", name="窗口内同轴统计异常点",
        marker=dict(color="#dc2626", symbol="diamond", size=9)), row=2, col=1)
    for value in (-threshold, threshold):
        figure.add_hline(y=value, line_dash="dash", line_color="#ea580c", row=2, col=1)
    figure.update_xaxes(title_text="相对所选事件开始时间（秒）", row=2, col=1)
    figure.update_layout(height=550, margin=dict(l=20, r=20, t=45, b=20))
    return share_time_hover(figure, "y2", "相对所选事件开始时间（秒）")


def render_event_review(samples, anomalies, selection, *, allow_bogie=False, fictional=False, anchors=None,
                        playback_scope=None):
    if not anomalies["events"]:
        st.session_state.pop("anomaly_review_event", None)
        st.session_state.pop("anomaly_review_fingerprint", None)
        return
    events = anomalies["events"]
    # The widget selects an event by index. Other observation changes affect
    # its freshly computed chart, but do not change which event the index names.
    legacy_events = [{key: value for key, value in event.items()
                      if key not in ("first_index", "last_index", "peak_index")} for event in events]
    fingerprint = selection_fingerprint(dict(selection=selection, threshold=anomalies["threshold"], events=legacy_events))
    if st.session_state.get("anomaly_review_fingerprint") != fingerprint:
        st.session_state.pop("anomaly_review_event", None)
        st.session_state["anomaly_review_fingerprint"] = fingerprint
    restored = st.session_state.pop(EVENT, None)
    if restored is None and "anomaly_review_event" not in st.session_state:
        restored = prior_event()
    if restored is not None:
        index, identity = restored
        if identity == fingerprint and index < len(events):
            st.session_state["anomaly_review_event"] = index
    with st.expander("异常事件回查 · 定位局部运行变化", expanded=allow_bogie):
        index = st.selectbox("选择异常事件", range(len(events)), key="anomaly_review_event",
                             format_func=lambda i: f"{i + 1}. {events[i]['type']} · {events[i]['start_timestamp']}")
        seconds = st.slider("事件前后窗口（秒）", 0, 60, 5, key="anomaly_review_seconds")
        window = event_window(samples, anomalies, index, seconds)
        window["first_row"] = playback_scope.first_row if playback_scope else 0
        event = window["event"]
        if allow_bogie:
            event_replay_button(label="在三维回放中查看此区段", key="run_analysis_bogie_jump",
                                vehicle_id=selection["vehicle_id"], batch_id=selection["batch_id"],
                                threshold=anomalies["threshold"], timestamp=event["start_timestamp"],
                                axis="lateral" if event["field"] == "lateral_accel" else "vertical",
                                fictional=fictional, row_span=selection.get("row_span"),
                                anchor=None if anchors is None else anchors[index], from_workbench=True,
                                scope=playback_scope)
            st.caption("三维页暂停在事件首条异常样本。可开启未超阈值对照，并返回本页继续分析。")
        st.table([{"类型": event["type"], "开始": event["start_timestamp"], "结束": event["end_timestamp"],
                   "异常样本数": event["sample_count"], "绝对峰值（m/s²）": event["maximum_absolute_value"]}])
        st.caption("紫色虚线为所选事件起止边界；红色标记为窗口内同轴既有异常点，也可能属于相邻事件。"
                   "重复时间戳仍保留；仅凭时间边界不能区分同一时刻的不同事件。")
        st.caption(f"窗口含 {len(window['rows'])} 条当前筛选内记录，绘制 {len(window['visible'])} 条；"
                   f"同轴异常点 {len(window['points'])} 个，绘制 {len(window['visible_points'])} 个。"
                   "各图层最多绘制 2,000 点，按序抽样可能省略局部细节；不插值、不重新检测。")
        plot_chart(event_chart(window, anomalies["threshold"]), width="stretch", key="anomaly_review_chart")
        st.caption(EVENT_DURATION_NOTE + "此视图用于数据回查，不构成故障原因或安全结论。")
    return index
