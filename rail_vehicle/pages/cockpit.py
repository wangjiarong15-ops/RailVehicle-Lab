"""Historical running-data cockpit; no device feeds or AI calls."""

import csv
import io
import sqlite3
from time import monotonic

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
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
    cols[0].metric("当前速度（km/h）", f"{sample['speed_kmh']:.1f}", border=True)
    cols[1].metric("横向加速度（m/s²）", f"{sample['lateral_accel']:.3f}", border=True)
    cols[2].metric("垂向加速度（m/s²）", f"{sample['vertical_accel']:.3f}", border=True)
    cols[3].metric("回放状态", state_label, border=True)
    if exceeded:
        st.error("当前样本数据超阈值：" + "、".join(exceeded))
    else:
        st.success("当前样本未超过设定的加速度阈值。")
    st.progress(state.position / track.duration if track.duration else 1.0,
                text=f"回放进度 {state.position:.1f} / {track.duration:.1f} 秒 · 样本 {index + 1}/{len(track.samples)}")
    st.caption(f"当前样本时间（UTC）：{sample['timestamp']} · 样本距回放时刻 {state.position - track.offsets[index]:.1f} 秒")
    gauge_col, chart_col = st.columns([1, 3])
    with gauge_col:
        gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=sample["speed_kmh"],
            title={"text": "速度 · km/h"},
            gauge={"axis": {"range": [0, max(1, max_speed, sample['speed_kmh']) * 1.1]},
                   "bar": {"color": "#2563eb"}},
        ))
        gauge.update_layout(height=260, margin=dict(l=25, r=25, t=45, b=15))
        st.plotly_chart(gauge, width="stretch", key="cockpit_gauge")
        st.caption("状态仅描述当前样本的统计阈值，不构成车辆故障或运行安全判断。")
    with chart_col:
        st.plotly_chart(build_replay_chart(track, index, state.position, threshold),
                        width="stretch", key="cockpit_curves")
    st.caption("曲线显示最近 60 秒、最多 1,000 条已回放记录；不插值、不补造样本。高倍速可能跳过部分仪表帧，已回放异常点仍在曲线上标红。")


def render() -> None:
    st.header("动态运行驾驶舱")
    st.info("历史数据模拟回放 · 使用已保存的运行样本，不代表真实实时监测。")
    source = st.radio("回放数据来源", demo_source_options("历史运行批次", "内置虚构演示"), horizontal=True,
                      key="cockpit_source")
    try:
        if source == "内置虚构演示":
            selection = ("demo",)
            max_speed = 120.0
            load_track = _demo_track
            st.caption("虚构城轨车辆 DEMO-001 · 120 条、每秒一条；不写入数据库。第 30–32 秒与 70–71 秒设置了测试波动。")
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

    threshold = st.number_input("加速度绝对值阈值（m/s²）", min_value=0.0,
                                value=1.0, step=0.1, key="cockpit_threshold")
    controls = st.columns([1, 1, 1, 2])
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
