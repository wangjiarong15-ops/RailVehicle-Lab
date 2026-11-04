"""Historical running-data cockpit; no device feeds or AI calls."""

import csv
import io
import sqlite3
from time import monotonic

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from rail_vehicle.ui import page_title
from rail_vehicle.ui import plot_chart, source_caption, chart_container, kpi
from rail_vehicle.deployment import demo_source_options

from rail_vehicle.demo_data import demo_csv_content
from rail_vehicle.replay import (
    ReplayState, ReplayTrack, advance_replay, control_replay, prepare_replay, sample_status,
)
from rail_vehicle.run_data import list_batch_samples, list_vehicle_batches
from rail_vehicle.vehicle_data import list_vehicles


STATE_KEY = "cockpit_state"
SELECTION_KEY = "cockpit_selection"


def _demo_track() -> ReplayTrack:
    return prepare_replay(list(csv.DictReader(io.StringIO(demo_csv_content(0)))))


def build_replay_chart(track: ReplayTrack, index: int, position: float, threshold: float):
    """Show observed samples only, bounded to the latest 60 seconds / 1,000 rows."""
    first = max(0, track.index_at(max(0, position - 60)), index - 999)
    rows = track.samples[first:index + 1]
    offsets = track.offsets[first:index + 1]
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.09)
    channels = (
        ("speed_kmh", "速度（km/h）", "#2563eb"),
        ("lateral_accel", "横向加速度（m/s²）", "#0891b2"),
        ("vertical_accel", "垂向加速度（m/s²）", "#7c3aed"),
    )
    for row_index, (field, label, color) in enumerate(channels, start=1):
        values = [row[field] for row in rows]
        figure.add_trace(go.Scatter(x=offsets, y=values, name=label,
                                   mode="lines+markers", marker_size=4,
                                   line_color=color), row=row_index, col=1)
        figure.update_yaxes(title_text=label, row=row_index, col=1)
        if row_index > 1:
            abnormal = [i for i, value in enumerate(values) if abs(value) > threshold]
            figure.add_trace(go.Scatter(
                x=[offsets[i] for i in abnormal], y=[values[i] for i in abnormal],
                mode="markers", marker={"color": "#dc2626", "size": 10, "symbol": "diamond"},
                name=f"{label}超阈值",
            ), row=row_index, col=1)
            for value in (-threshold, threshold):
                figure.add_hline(y=value, line_dash="dash", line_color="#dc2626",
                                 row=row_index, col=1)
    figure.update_xaxes(range=[max(0, position - 60), max(10, position + 1)])
    figure.update_xaxes(title_text="相对运行时间（秒）", row=3, col=1)
    figure.update_layout(height=570, showlegend=False, margin=dict(l=20, r=20, t=15, b=20))
    return figure


def build_response_schematic(sample, threshold):
    """Symbolic direction and threshold status; no displacement is inferred."""
    figure = go.Figure()
    # Fixed geometry: the drawings never translate/rotate in response to acceleration.
    figure.add_shape(type="path", path="M 0.9,3.3 L 8.8,3.3 Q 9.3,3.3 9.15,3.9 L 8.6,5.05 Q 8.3,5.5 7.8,5.5 L 1.3,5.5 Q 0.9,5.5 0.9,5.1 Z",
                     line=dict(color="#58a6ff", width=2), fillcolor="rgba(74,130,192,0.16)")
    figure.add_shape(type="line", x0=1, x1=8.9, y0=3.85, y1=3.85,
                     line=dict(color="#41b8c8", width=3))
    for x in (1.5, 2.8, 5.5, 6.8):
        figure.add_shape(type="rect", x0=x, x1=x+.85, y0=4.4, y1=5.05,
                         line=dict(color="#58a6ff", width=1), fillcolor="rgba(54,143,212,0.24)")
    figure.add_shape(type="path", path="M 8.05,5.08 L 8.45,5.0 L 8.8,4.42 L 8.05,4.42 Z",
                     line=dict(color="#58a6ff", width=1), fillcolor="rgba(54,143,212,0.24)")
    figure.add_shape(type="rect", x0=4.05, x1=5.05, y0=3.4, y1=5.1,
                     line=dict(color="#809bb9", width=1), fillcolor="rgba(0,0,0,0)")
    figure.add_shape(type="line", x0=4.55, x1=4.55, y0=3.4, y1=5.1,
                     line=dict(color="#809bb9", width=1))
    for x in (2.5, 7.4):
        figure.add_shape(type="rect", x0=x-.8, x1=x+.8, y0=2.95, y1=3.3,
                         line=dict(color="#809bb9", width=1), fillcolor="#273c56")
        for wheel in (x-.5, x+.5):
            figure.add_shape(type="circle", x0=wheel-.24, x1=wheel+.24, y0=2.57, y1=3.05,
                             line=dict(color="#91a9c9", width=2), fillcolor="#273c56")
    figure.add_shape(type="line", x0=.4, x1=9.6, y0=2.53, y1=2.53,
                     line=dict(color="#809bb9", width=1))
    figure.add_annotation(x=5, y=6.25, text="车辆侧视 · 非物理位移模型", showarrow=False,
                          font=dict(size=11))
    for field, label, x, y in (("lateral_accel", "横向", 5, 1.6),
                               ("vertical_accel", "垂向", 5, .65)):
        value = sample[field]
        marker = "●" if value == 0 else ("→" if value > 0 else "←") if field == "lateral_accel" else ("↑" if value > 0 else "↓")
        color = "#f46d7a" if abs(value) > threshold else "#58a6ff"
        figure.add_annotation(x=x, y=y, text=f"{label} {marker} {value:+.3f} m/s²",
                              showarrow=False, font=dict(color=color, size=14))
    figure.update_layout(height=230, margin=dict(l=5, r=5, t=5, b=5),
                         xaxis=dict(range=[0, 10], visible=False, fixedrange=True),
                         yaxis=dict(range=[0, 7], visible=False, fixedrange=True),
                         showlegend=False)
    return figure


def _render_instruments(track: ReplayTrack, threshold: float, max_speed: float) -> None:
    previous = st.session_state[STATE_KEY]
    state = advance_replay(previous, monotonic(), track.duration)
    st.session_state[STATE_KEY] = state
    if previous.playing and not state.playing:
        st.rerun()  # Stop the timer and refresh the control buttons at end-of-run.
    index = track.index_at(state.position)
    sample = track.samples[index]
    exceeded = sample_status(sample, threshold)
    state_label = "播放中" if state.playing else (
        "回放结束" if state.position >= track.duration else "已暂停"
    )
    cols = st.columns(4)
    kpi(cols[0], "当前速度（km/h）", f"{sample['speed_kmh']:.1f}", "当前历史采样点")
    kpi(cols[1], "横向加速度（m/s²）", f"{sample['lateral_accel']:.3f}", "横向通道 · 带符号原值")
    kpi(cols[2], "垂向加速度（m/s²）", f"{sample['vertical_accel']:.3f}", "垂向通道 · 带符号原值")
    kpi(cols[3], "回放状态", state_label, f"{state.rate:g}× · 历史数据模拟")
    if exceeded:
        st.error("当前样本数据超阈值：" + "、".join(exceeded))
    else:
        st.success("当前样本未超过设定的加速度阈值。")
    st.progress(state.position / track.duration if track.duration else 1.0,
                text=f"回放进度 {state.position:.1f} / {track.duration:.1f} 秒 · 样本 {index + 1}/{len(track.samples)}")
    st.caption(f"当前样本时间（UTC）：{sample['timestamp']} · 样本距回放时刻 {state.position - track.offsets[index]:.1f} 秒")
    gauge_col, chart_col = st.columns([1.15, 2.85])
    with gauge_col, chart_container("rv-panel-cockpit-instruments"):
        st.subheader("车辆状态")
        st.caption("当前样本 / 仪表与响应示意")
        gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=sample["speed_kmh"],
            title={"text": "速度 · km/h"},
            gauge={"axis": {"range": [0, max(1, max_speed, sample['speed_kmh']) * 1.1]},
                   "bar": {"color": "#58a6ff", "thickness": .22},
                   "bgcolor": "rgba(111,146,181,0.1)", "borderwidth": 0},
        ))
        gauge.update_layout(height=210, margin=dict(l=20, r=20, t=45, b=10))
        plot_chart(gauge, width="stretch", key="cockpit_gauge")
        st.markdown("**车辆响应示意**")
        plot_chart(build_response_schematic(sample, threshold), width="stretch", key="cockpit_schematic",
                   config={"displayModeBar": False})
        st.caption("箭头仅表示当前加速度符号，颜色表示是否超阈值；图形不表示位移或姿态。")
        st.caption("状态仅描述当前样本的统计阈值，不构成车辆故障或运行安全判断。")
    with chart_col, chart_container("rv-panel-cockpit-curves"):
        st.subheader("运行信号")
        st.caption("最近 60 秒 · 红色菱形为超阈值采样点")
        plot_chart(build_replay_chart(track, index, state.position, threshold),
                        width="stretch", key="cockpit_curves")
    st.caption("曲线显示最近 60 秒、最多 1,000 条已回放记录；不插值、不补造样本。高倍速可能跳过部分仪表帧，已回放异常点仍在曲线上标红。")


def render() -> None:
    page_title("动态运行驾驶舱", "HISTORICAL REPLAY / 历史运行回放")
    source_col, threshold_col = st.columns([2, 1], vertical_alignment="bottom")
    with source_col:
        source = st.radio("回放数据来源", demo_source_options("历史运行批次", "内置虚构演示"), horizontal=True,
                          key="cockpit_source")
    with threshold_col:
        threshold = st.number_input("加速度绝对值阈值（m/s²）", min_value=0.0,
                                    value=1.0, step=0.1, key="cockpit_threshold")
    source_caption(fictional=source == "内置虚构演示")
    with st.expander("回放说明"):
        st.info("历史数据模拟回放 · 使用已保存的运行样本，不代表真实实时监测。")
        if source == "内置虚构演示":
            st.caption("虚构城轨车辆 DEMO-001 · 120 条、每秒一条；不写入数据库。第 30–32 秒与 70–71 秒设置了测试波动。")
    try:
        if source == "内置虚构演示":
            selection = ("demo",)
            max_speed = 120.0
            load_track = _demo_track
        else:
            vehicles = list_vehicles()
            if not vehicles:
                st.session_state.pop(STATE_KEY, None)
                st.info("尚无车辆。可先登记车辆并导入 CSV，或选择“内置虚构演示”直接体验。")
                return
            vehicle_id = st.selectbox("选择车辆", [v["id"] for v in vehicles],
                                      format_func=lambda vid: next(v["vehicle_code"] for v in vehicles if v["id"] == vid),
                                      key="cockpit_vehicle")
            vehicle = next(v for v in vehicles if v["id"] == vehicle_id)
            batches = list_vehicle_batches(vehicle_id)
            if not batches:
                st.session_state.pop(STATE_KEY, None)
                st.info("该车辆尚无历史运行批次，请先到“运行分析”导入 CSV，或选择内置演示。")
                return
            batch_id = st.selectbox("选择运行批次", [b["id"] for b in batches],
                                    format_func=lambda bid: next(f"批次 {b['id']} · {b['file_name']} · {b['sample_count']} 条" for b in batches if b["id"] == bid),
                                    key=f"cockpit_batch_{vehicle_id}")
            selection = (vehicle_id, batch_id)
            max_speed = float(vehicle.get("max_speed_kmh") or 120)
            load_track = lambda: prepare_replay(list_batch_samples(vehicle_id, batch_id))
        if st.session_state.get(SELECTION_KEY) != selection or "cockpit_track" not in st.session_state:
            st.session_state[STATE_KEY] = ReplayState()
            st.session_state["cockpit_track"] = load_track()
            st.session_state[SELECTION_KEY] = selection
        track = st.session_state["cockpit_track"]
    except (ValueError, TypeError, KeyError, OverflowError):
        st.session_state.pop(STATE_KEY, None)
        st.session_state.pop("cockpit_track", None)
        st.error("所选批次没有可回放的有效数据，请检查时间戳和数值后重新导入。")
        return
    except (sqlite3.Error, OSError):
        st.session_state.pop(STATE_KEY, None)
        st.error("暂时无法读取历史运行数据，请稍后重试。")
        return

    controls = st.columns([1, 1, 1, 2], vertical_alignment="bottom")
    state = st.session_state.get(STATE_KEY, ReplayState())
    action = None
    if controls[0].button("播放", icon=":material/play_arrow:", type="primary", disabled=state.playing or track.duration == 0):
        action = "play"
    if controls[1].button("暂停", icon=":material/pause:", disabled=not state.playing):
        action = "pause"
    if controls[2].button("重置", icon=":material/restart_alt:"):
        action = "reset"
    rate = controls[3].selectbox("回放速度", [0.5, 1.0, 2.0, 4.0, 8.0], index=1,
                                 format_func=lambda value: f"{value:g}×", key="cockpit_rate")
    if rate != state.rate:
        state = control_replay(state, "rate", monotonic(), track.duration, rate=rate)
    if action:
        state = control_replay(state, action, monotonic(), track.duration)
    st.session_state[STATE_KEY] = state
    if action:
        st.rerun()
    if track.duration == 0:
        st.info("所有记录位于同一时刻，当前仅展示最后一条样本，无可播放的时间跨度。")
    st.fragment(run_every=0.5 if state.playing else None)(_render_instruments)(track, threshold, max_speed)
