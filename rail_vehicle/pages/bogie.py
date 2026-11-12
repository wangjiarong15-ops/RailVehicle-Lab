"""Offline teaching and historical replay of the simplified bogie."""

from bisect import bisect_left, bisect_right
from datetime import timezone
import sqlite3
from time import monotonic

import streamlit as st

from rail_vehicle.bogie import PART_DESCRIPTIONS, PARTS, build_bogie
from rail_vehicle.bogie_replay import GAP, TEACHING, event_positions, gap_cutoff, illustration_phase, replay_scene
from rail_vehicle.demo_data import DEMO_BATCH_IDS, analysis_demo_data
from rail_vehicle.deployment import demo_source_options
from rail_vehicle.pages.cockpit import build_replay_chart
from rail_vehicle.replay import ReplayState, advance_replay, control_replay, prepare_replay
from rail_vehicle.run_data import list_batch_samples, list_vehicle_batches
from rail_vehicle.timestamp_utils import parse_iso_timestamp
from rail_vehicle.ui import navigate, page_title, plot_chart
from rail_vehicle.vehicle_data import list_vehicles

STATE_KEY = "bogie_playback"
FRAME_KEY = "bogie_paused_row"
DEMO = "内置虚构演示"
HISTORY = "历史运行批次"
TEACH = "独立教学动画"


def _seek(position, row=None):
    rate = st.session_state.get(STATE_KEY, ReplayState()).rate
    st.session_state[STATE_KEY] = ReplayState(position=position, rate=rate)
    st.session_state[FRAME_KEY] = row


def _load(source, target):
    if source == DEMO:
        if target and target["batch_id"] in DEMO_BATCH_IDS:
            st.session_state["bogie_demo_batch"] = target["batch_id"]
        batch = st.selectbox("虚构批次", DEMO_BATCH_IDS, format_func=lambda item: f"虚构批次 {-item}",
                             key="bogie_demo_batch")
        return (DEMO, batch), lambda: prepare_replay(analysis_demo_data()[2][batch])
    if source == TEACH:
        return (TEACH,), None
    vehicles = list_vehicles()
    if not vehicles:
        st.info("请先登记车辆并导入 CSV，或选择虚构演示。")
        return (HISTORY,), None
    labels = {row["id"]: row["vehicle_code"] for row in vehicles}
    if target and target["vehicle_id"] in labels:
        st.session_state["bogie_vehicle"] = target["vehicle_id"]
    vehicle = st.selectbox("车辆", list(labels), format_func=labels.get, key="bogie_vehicle")
    batches = list_vehicle_batches(vehicle)
    if not batches:
        st.info("该车辆没有历史运行批次。")
        return (HISTORY, vehicle), None
    labels = {row["id"]: f"批次 {row['id']} · {row['file_name']}" for row in batches}
    key = f"bogie_batch_{vehicle}"
    if target and target["batch_id"] in labels:
        st.session_state[key] = target["batch_id"]
    batch = st.selectbox("批次", list(labels), format_func=labels.get, key=key)
    return (HISTORY, vehicle, batch), lambda: prepare_replay(list_batch_samples(vehicle, batch))


def _display(track, duration, threshold, cutoff, teaching, comparison, visible, selected, view, labels):
    state = st.session_state[STATE_KEY]
    now = monotonic()
    if track is None and state.playing and state.last_tick is not None:
        state = ReplayState((state.position + max(0, now - state.last_tick) * state.rate) % duration,
                            True, state.rate, now)
    else:
        previous = state
        state = advance_replay(state, now, duration)
        if previous.playing and not state.playing:
            st.session_state[STATE_KEY] = state
            st.rerun()
    st.session_state[STATE_KEY] = state
    index = None
    if track is not None:
        index = st.session_state.get(FRAME_KEY)
        if index is None:
            index = track.index_at(state.position)
        sample = track.samples[index]
        scene = replay_scene(track, state.position, index, threshold, cutoff)
    else:
        scene = TEACHING[teaching]
    highlight = selected if selected != "无" else scene.parts
    phase = 0 if scene is GAP else illustration_phase(state.position)
    model, panel = st.columns([3, 1.25])
    with model, st.container(border=True, key="bogie_model_card"):
        st.subheader("三维结构")
        st.caption("拖动旋转 · 滚轮缩放 · 悬停查看名称")
        compare = comparison and bool(scene.parts)
        if not visible:
            st.info("请至少选择一个部件。回放和读值仍会更新。")
        else:
            try:
                if compare:
                    active, reference = st.columns(2)
                    active.caption("当前场景")
                    reference.caption("同一相位 · 正常参照")
                else:
                    active = st.container()
                height = 340 if compare else 500
                with active:
                    plot_chart(build_bogie(scene.mode, phase, visible, highlight, view, labels=labels, height=height),
                               width="stretch", key="bogie_model", config={"scrollZoom": True})
                if compare:
                    with reference:
                        plot_chart(build_bogie("正常运行", phase, visible, "无", view, height=height),
                                   width="stretch", key="bogie_reference", config={"scrollZoom": True})
            except (TypeError, ValueError, RuntimeError):
                st.error("三维视图暂时无法加载，仍可查看运行信号。")
    with panel, st.container(border=True, key="bogie_status_card"):
        if track is not None:
            st.subheader("间隔前最后一次观测" if scene is GAP else "当前观测样本")
            st.caption(f"{sample['timestamp']} · 第 {index + 1:,}/{len(track.samples):,} 条")
            st.metric("速度（km/h）", f"{sample['speed_kmh']:.1f}")
            st.metric("横向加速度（m/s²）", f"{sample['lateral_accel']:.3f}")
            st.metric("垂向加速度（m/s²）", f"{sample['vertical_accel']:.3f}")
        st.subheader("状态与检查")
        if scene is GAP or (track is not None and scene.parts):
            st.warning(scene.label)
        else:
            st.write(scene.label if track is not None else teaching)
        st.write("涉及结构：" + ("、".join(scene.parts) or "无"))
        if track is not None or scene.parts:
            st.caption(scene.impact)
            st.write("建议：" + scene.check)
        if selected != "无":
            st.caption(f"部件说明 · {selected}：{PART_DESCRIPTIONS[selected]}")
            if selected not in visible:
                st.caption("该部件已隐藏，可在左侧重新显示。")
        st.progress(state.position / duration if duration else 1.0,
                    text=f"{state.position:.2f} / {duration:.2f} 秒")
    if track is not None:
        with st.container(border=True, key="bogie_signal_card"):
            st.subheader("运行信号")
            st.caption("青色线为当前观测；红色菱形为超阈值样本。两次采样之间不补算数据。")
            chart = build_replay_chart(track, index, state.position, threshold)
            chart.add_vline(x=track.offsets[index], line_color="#39c4ce", line_dash="dash")
            plot_chart(chart, width="stretch", key="bogie_curves")


def render():
    page_title("三维转向架仿真", "结构观察 / 运动教学 / 历史回放")
    st.info("三维几何和运动均为教学示意。历史样本只决定提示和场景，不反推真实位移、轮轨力或故障。")
    target = st.session_state.pop("bogie_evidence_target", None)
    batch_target = st.session_state.pop("bogie_batch_target", None)
    choices = [TEACH, *demo_source_options(HISTORY, DEMO)]
    fictional = bool(target and target.get("fictional") is True)
    requested = target if fictional else (target or batch_target) if HISTORY in choices else None
    if requested:
        st.session_state["bogie_source"] = DEMO if fictional else HISTORY
    if target and requested:
        st.session_state["bogie_threshold"] = target["threshold"]
    options, main = st.columns([1, 4])
    with options:
        st.subheader("场景与结构")
        source = st.radio("数据来源", choices, key="bogie_source")
        teaching = st.selectbox("教学场景", list(TEACHING), disabled=source != TEACH)
        with st.expander("视角与部件"):
            view = st.selectbox("观察视角", ["整体视图", "侧视图", "正视图", "俯视图"])
            selected = st.selectbox("手动高亮部件", ["无", *PARTS])
            visible = st.multiselect("显示部件", PARTS, default=list(PARTS))
            comparison = st.checkbox("对照正常运动")
            labels = st.toggle("显示部件标注", key="bogie_labels")
        if source == DEMO:
            st.caption("虚构数据，非实际测量。")
        try:
            selection, load = _load(source, requested)
            if selection != st.session_state.get("bogie_selection"):
                track = load() if load else None
                st.session_state["bogie_track"] = track
                st.session_state["bogie_gap_cutoff"] = gap_cutoff(track) if track else None
                st.session_state["bogie_selection"] = selection
                st.session_state.pop("bogie_events", None)
                _seek(0)
            track = st.session_state.get("bogie_track")
        except (ValueError, TypeError, KeyError, OverflowError, sqlite3.Error, OSError):
            st.error("无法读取有效的运行数据，请检查批次后重试。")
            track = None
        if "bogie_threshold" not in st.session_state:
            st.session_state["bogie_threshold"] = 1.0
        threshold = st.number_input("加速度绝对值阈值（m/s²）", min_value=0.0,
                                    value=None, step=0.1, key="bogie_threshold", disabled=track is None)
        if threshold is None:
            threshold = 1.0
            st.caption("输入为空，使用默认阈值 1.0 m/s²。")
        if source == DEMO and track is not None:
            st.button("核查虚构证据 →", key="bogie_demo_evidence", on_click=navigate,
                      args=("AI 智能分析中心",), kwargs={
                          "ai_center_demo": True, "ai_demo_batch": selection[1],
                          "ai_demo_threshold": threshold,
                      })
    if target and requested:
        row = None
        expected = ((DEMO, target["batch_id"]) if fictional else
                    (HISTORY, target["vehicle_id"], target["batch_id"]))
        if track is not None and selection == expected:
            timestamp = parse_iso_timestamp(target["timestamp"])
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            start = (timestamp - parse_iso_timestamp(track.samples[0]["timestamp"])).total_seconds()
            field = {"lateral": "lateral_accel", "vertical": "vertical_accel"}.get(target["axis"])
            row = next((i for i in range(bisect_left(track.offsets, start), bisect_right(track.offsets, start))
                        if field and abs(track.samples[i][field]) > threshold), None)
        if row is not None:
            _seek(track.offsets[row], row)
            st.success("已定位到虚构演示的异常样本。" if fictional else
                       "已定位到所选统计证据的异常样本。")
        else:
            st.warning("当前批次未找到对应的异常样本，请回到运行分析重新核查证据。")
    with main:
        state = st.session_state.get(STATE_KEY, ReplayState())
        duration = track.duration if track is not None else 2.4
        if source != TEACH and track is None:
            st.caption("暂无可回放的样本；仍可观察教学模型。")
        with st.container(horizontal=True, vertical_alignment="bottom"):
            action = None
            if st.button("播放", icon=":material/play_arrow:", type="primary",
                         disabled=state.playing or duration == 0):
                action = "play"
            if st.button("暂停", icon=":material/pause:", disabled=not state.playing):
                action = "pause"
            if st.button("重置", icon=":material/restart_alt:"):
                action = "reset"
            rate = st.selectbox("播放速度", [0.5, 1., 2., 4.], index=1, width=110,
                                format_func=lambda x: f"{x:g}×")
        if rate != state.rate:
            state = control_replay(state, "rate", monotonic(), duration, rate=rate)
        if action:
            state = control_replay(state, action, monotonic(), duration)
            if action in ("play", "reset"):
                st.session_state[FRAME_KEY] = None
        st.session_state[STATE_KEY] = state
        if action:
            st.rerun()
        if track is not None:
            st.session_state["bogie_seek"] = state.position
            st.slider("时间轴定位（秒）", 0., max(duration, 0.001), key="bogie_seek",
                      disabled=state.playing or duration == 0,
                      on_change=lambda: _seek(st.session_state["bogie_seek"]))
            row = st.session_state.get(FRAME_KEY)
            row = track.index_at(state.position) if row is None else row
            prev, nxt = st.columns(2)
            for column, label, step in ((prev, "上一帧", -1), (nxt, "下一帧", 1)):
                i = row + step
                if column.button(label, disabled=state.playing or not 0 <= i < len(track.samples)):
                    _seek(track.offsets[i], i)
                    st.rerun()
        view_slot = st.container()
        if track is not None:
            cache = st.session_state.get("bogie_events")
            if not cache or cache[0] != threshold:
                cache = (threshold, event_positions(track, threshold))
                st.session_state["bogie_events"] = cache
            events = cache[1]
            with st.expander(f"定位超阈值区段（{len(events):,}）"):
                if events:
                    page = st.number_input("区段页码", 1, (len(events) + 99) // 100, 1)
                    first = (page - 1) * 100
                    chosen = st.selectbox("统计异常区段", range(first, min(first+100, len(events))),
                                          format_func=lambda i: f"{i+1} · {events[i][0]['type']} · {events[i][0]['start_timestamp']}")
                    if st.button("定位区段起点"):
                        i = events[chosen][1]
                        _seek(track.offsets[i], i)
                        st.rerun()
        with view_slot:
            st.fragment(run_every=(0.15 if track is None else 0.4) if state.playing else None)(_display)(
                track, duration, threshold, st.session_state.get("bogie_gap_cutoff"),
                teaching, comparison, visible, selected, view, labels)
