"""Offline teaching and historical replay of the simplified bogie."""

from dataclasses import dataclass
from secrets import token_urlsafe
import sqlite3
from time import monotonic

import streamlit as st

from rail_vehicle.bogie import PART_DESCRIPTIONS, PARTS, VIEWS, build_bogie
from rail_vehicle.bogie_inspection_context import inspection_context
from rail_vehicle.bogie_replay import (
    EVENT_FILTERS, EventLoop, EventSpanIndex, GAP, ILLUSTRATION_PERIOD, TEACHING, advance_event_loop, advance_teaching,
    control_event_loop, control_teaching, event_loop, event_positions, gap_cutoff, illustration_phase,
    replay_event_catalog, replay_scene,
)
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.demo_data import DEMO_BATCH_IDS, analysis_demo_data
from rail_vehicle.deployment import demo_source_options
from rail_vehicle.event_anchor import resolve_event_row
from rail_vehicle.pages.cockpit import build_replay_chart
from rail_vehicle.pages.data_workflow import WORKBENCH_RETURN, analysis_button, render_replay_return, return_to_workbench
from rail_vehicle.replay import ReplayState, advance_replay, comparison_reference, control_replay, prepare_replay
from rail_vehicle.replay_evidence import (
    MAX_WINDOW_EXPORT_ROWS, ReplayEvidenceScope, ReportComparison, comparison_evidence_csv, replay_evidence_csv,
)
from rail_vehicle.run_data import list_batch_samples, list_vehicle_batches
from rail_vehicle.ui import navigate, page_title, plot_chart
from rail_vehicle.vehicle_data import list_vehicles

STATE_KEY = "bogie_playback"
FRAME_KEY = "bogie_paused_row"
LOOP_KEY = "bogie_event_loop"
EVIDENCE_KEY = "bogie_window_csv"
REVIEW_KEY = "bogie_related_review"
EVENTS_PER_PAGE = 100
DEMO = "内置虚构演示"
HISTORY = "历史运行批次"
TEACH = "独立教学动画"
WEBGL = "Three.js 工作台"
COMPAT = "Plotly 兼容模式"
_PRESETS = {
    "完整转向架": ("整体视图", PARTS, "观察各总成的整体装配关系。"),
    "轮对与轴箱": ("整体视图", ("轮对", "轴箱"), "从外侧观察轴箱端盖，从内侧观察轮缘和车轴。"),
    "悬挂支承": ("侧视图", ("构架", "一系悬挂", "二系悬挂", "轴箱"), "对照轴箱上方的一系悬挂与构架上方的二系空气弹簧。"),
    "传动与制动": ("底视图", ("轮对", "电机与传动", "制动装置"), "从下方观察电机、齿轮箱、制动盘和卡钳相对于车轴的位置。"),
}


@dataclass(frozen=True)
class Inspection:
    view: str
    visible: tuple[str, ...]
    focus: str | None
    labels: bool
    comparison: bool
    exploded: bool
    spread: float
    rails: bool
    camera_revision: int


def _model_view(mode, phase, state, inspection, highlight, height, key, renderer, continuous,
                *, labels=False, focus=None, exploded=False, export_context=None, reference=False, periodic=False,
                related_review=(), camera_group=None):
    camera = dict(height=height, rails=inspection.rails, camera_revision=inspection.camera_revision)
    if renderer == WEBGL:
        render_webgl(mode=mode, phase=phase, state=state, visible=inspection.visible, highlight=highlight,
                     view=inspection.view, focus=focus, labels=labels, exploded=exploded,
                     spread=inspection.spread, continuous=continuous and not exploded, key=key,
                     export_context=export_context, reference=reference, periodic=periodic,
                     related_review=related_review, camera_group=camera_group, **camera)
    else:
        plot_chart(build_bogie(mode, phase, inspection.visible, highlight, inspection.view,
                              labels=labels, exploded=exploded, spread=inspection.spread,
                              focus=focus, **camera), width="stretch",
                   key=f"{key}_{inspection.camera_revision}", config={"scrollZoom": True})


def _reset_camera():
    st.session_state["bogie_camera_revision"] = st.session_state.get("bogie_camera_revision", 0) + 1


def _apply_preset():
    view, parts, _ = _PRESETS[st.session_state["bogie_inspection_preset"]]
    st.session_state.update(bogie_view=view, bogie_visible_parts=list(parts),
                            bogie_focus="无", bogie_isolate=False)
    _reset_camera()


def _inspection_controls(source, comparison):
    preset = st.selectbox("观察预设", list(_PRESETS), key="bogie_inspection_preset", on_change=_apply_preset)
    st.caption(_PRESETS[preset][2])
    st.button("恢复所选预设", key="bogie_preset_reset", on_click=_apply_preset, width="stretch")
    view = st.selectbox("观察视角", VIEWS, key="bogie_view")
    st.button("复位当前视角", key="bogie_camera_reset", on_click=_reset_camera, width="stretch")
    selected = st.selectbox("手动高亮部件", ["无", *PARTS], key="bogie_focus")
    focus = None if selected == "无" else selected
    st.session_state.setdefault("bogie_visible_parts", list(PARTS))
    parts = st.multiselect("显示部件", PARTS, key="bogie_visible_parts")
    isolate = st.toggle("仅显示当前部件", key="bogie_isolate", disabled=focus is None) and focus is not None
    labels = st.toggle("显示部件标注", key="bogie_labels")
    exploded = st.toggle("分解示意", key="bogie_exploded", disabled=source != TEACH) and source == TEACH
    spread = st.slider("构件分离程度", 20, 150, 100, step=10, format="%d%%",
                       key="bogie_spread") / 100 if exploded else 1.0
    rails_available = not exploded and not isolate and view != "底视图"
    rails = st.toggle("显示轨道参照", value=True, key="bogie_reference_rails", disabled=not rails_available)
    if exploded:
        st.caption("分解示意会暂停教学动画；分离程度只影响示意图。")
    elif view == "底视图":
        st.caption("底视图自动隐藏轨道，便于观察下方构件。")
    return Inspection(view, (focus,) if isolate else tuple(parts), focus, labels, comparison,
                      exploded, spread, rails and rails_available, st.session_state.get("bogie_camera_revision", 0))


def _seek(position, row=None):
    rate = st.session_state.get(STATE_KEY, ReplayState()).rate
    st.session_state[STATE_KEY] = ReplayState(position=position, rate=rate)
    st.session_state[FRAME_KEY] = row
    st.session_state.pop(REVIEW_KEY, None)


def _clear_loop(*, pause=False):
    old = st.session_state.pop(LOOP_KEY, None)
    st.session_state.pop(EVIDENCE_KEY, None)
    if old and pause and STATE_KEY in st.session_state:
        position = st.session_state[STATE_KEY].position
        _seek(position, _current_row(old["track"], position, old["window"]))


def _current_row(track, position, window):
    row = st.session_state.get(FRAME_KEY)
    if row is not None:
        return row
    return window.index_at(track, position) if window else track.index_at(position)


def _open_window(track, threshold, window, *, autoplay=False):
    _clear_loop()
    _seek(window.start, None if autoplay else window.first_row)
    st.session_state[LOOP_KEY] = dict(track=track, threshold=threshold, window=window)
    if autoplay:
        st.session_state[STATE_KEY] = control_event_loop(st.session_state[STATE_KEY], "play", monotonic(), window)


def _window_evidence(track, window, selection, threshold):
    scope = ReplayEvidenceScope.from_window(track, window)
    fictional = selection[0] == DEMO
    batch_id = selection[-1]
    vehicle_id = None if fictional else selection[1]
    with st.expander(f"窗口证据与导出（{scope.count:,} 条）"):
        st.caption(f"按批次时间排序：第 {scope.first_row + 1:,}–{scope.last_row + 1:,} 条。"
                   "保留同一时间戳的不同样本；不导出插值或动画数值。")
        if fictional:
            st.caption("虚构演示数据，非实际测量；导出文件也会标记来源。")
        else:
            analysis_button(st, label="在运行分析中查看同一窗口 →", key="bogie_window_analysis",
                            vehicle_id=vehicle_id, batch_id=batch_id, threshold=threshold, scope=scope)
        if scope.count > MAX_WINDOW_EXPORT_ROWS:
            st.info(f"每次最多导出 {MAX_WINDOW_EXPORT_ROWS:,} 条，请缩小窗口再导出。")
            return
        if st.button("准备窗口 CSV", key="bogie_prepare_window_csv"):
            st.session_state[EVIDENCE_KEY] = replay_evidence_csv(
                track, scope, batch_id=batch_id, vehicle_id=vehicle_id, threshold=threshold, fictional=fictional)
        if csv_data := st.session_state.get(EVIDENCE_KEY):
            origin = "fictional" if fictional else "historical"
            filename = f"rail-{origin}-batch-{abs(batch_id)}-rows-{scope.first_row+1}-{scope.last_row+1}.csv"
            st.download_button("下载窗口 CSV", csv_data, file_name=filename, mime="text/csv",
                               key="bogie_download_window_csv", on_click="ignore")


def _jump_to_catalog_entry(position, row, entry, page_key, choice_key):
    _clear_loop()
    _seek(position, row)
    st.session_state[page_key] = entry // EVENTS_PER_PAGE + 1
    st.session_state[choice_key] = entry


def _event_explorer(track, cache, threshold, state, window):
    catalog = cache["catalog"]
    with st.expander(f"定位超阈值区段（{len(catalog['axes'].entries):,}）"):
        if not catalog["axes"].entries:
            st.caption("当前阈值下没有横向或垂向超阈值区段。")
            return
        direction = st.selectbox("区段方向", list(EVENT_FILTERS), format_func=EVENT_FILTERS.get,
                                 key="bogie_event_direction")
        st.caption(f"横向 {len(catalog['lateral'].entries):,} 段 · 垂向 {len(catalog['vertical'].entries):,} 段 · "
                   f"双轴同点 {len(catalog['combined'].entries):,} 段。双轴同点要求同一条观测的两轴都超阈值，"
                   "不与分轴数量相加。")
        view = catalog[direction]
        if not view.entries:
            st.caption("此方向没有符合条件的区段。")
            return
        page_key, choice_key = f"bogie_event_page_{direction}", f"bogie_event_choice_{direction}"
        current_row = _current_row(track, state.position, window)
        left, right = st.columns(2)
        for column, label, item in zip((left, right), ("上一异常起点", "下一异常起点"), view.neighbors(current_row)):
            if item is None or state.playing:
                column.button(label, disabled=True, width="stretch")
            else:
                row = view.starts[item]
                column.button(label, on_click=_jump_to_catalog_entry,
                              args=(track.offsets[row], row, item, page_key, choice_key), width="stretch")
        st.caption("暂停后可按当前样本行跳到更早或更晚的区段起点。")
        pages = (len(view.entries) + EVENTS_PER_PAGE - 1) // EVENTS_PER_PAGE
        if pages > 1:
            page = st.number_input("区段页码", min_value=1, max_value=pages, key=page_key)
        else:
            page = 1
        first = (page - 1) * EVENTS_PER_PAGE
        options = range(first, min(first + EVENTS_PER_PAGE, len(view.entries)))
        if st.session_state.get(choice_key) not in options:
            st.session_state[choice_key] = first

        def entry_label(item):
            event, row = view.entries[item]
            return f"{item + 1} · {event['type']} · 第 {row+1:,} 条 · {event['start_timestamp']}"

        chosen = st.selectbox("统计异常区段", options, index=None, key=choice_key, format_func=entry_label)
        if chosen is None:
            return
        event, first_sample = view.entries[chosen]
        last_sample = first_sample + event["sample_count"] - 1
        st.caption(f"第 {first_sample+1:,}–{last_sample+1:,} 条 · 共 {event['sample_count']:,} 条 · "
                   f"首末时间差 {event['duration_seconds']:.6g} 秒；采样空档内没有观测。")
        if direction == "combined":
            peaks = event["peaks"]
            st.caption(f"各轴绝对峰值所在样本的带符号读数：横向 {peaks['lateral_accel']:.4g} m/s²，"
                       f"垂向 {peaks['vertical_accel']:.4g} m/s²；两个峰值可能来自不同样本。")
        else:
            st.caption(f"绝对峰值所在样本的带符号读数：{event['maximum_signed_value']:.4g} m/s²。")
        if st.button("定位区段起点"):
            _clear_loop()
            _seek(track.offsets[first_sample], first_sample)
            st.rerun()
        before_col, after_col = st.columns(2)
        before = before_col.number_input("异常前保留（秒）", 0., 60., 2., step=.5, key="bogie_loop_before")
        after = after_col.number_input("异常后保留（秒）", 0., 60., 2., step=.5, key="bogie_loop_after")
        candidate = event_loop(track, event, first_sample, before, after)
        if not candidate.timed:
            st.caption("窗口内样本时间相同，可逐条核查和导出；没有可播放的时长。")
        if st.button("循环回放此区段" if candidate.timed else "逐帧核查此区段", key="bogie_start_loop"):
            _open_window(track, threshold, candidate, autoplay=candidate.timed)
            st.rerun()


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


def _display(track, duration, threshold, cutoff, teaching, inspection, event_index, show_signals,
             window, renderer, source, selection, camera_group, speed_tolerance):
    state = st.session_state[STATE_KEY]
    now = monotonic()
    if window:
        state = advance_event_loop(state, now, window)
    elif track is None:
        state = advance_teaching(state, now)
    else:
        previous = state
        state = advance_replay(state, now, duration)
        if previous.playing and not state.playing:
            st.session_state[STATE_KEY] = state
            st.rerun()
    st.session_state[STATE_KEY] = state
    index = None
    sample = None
    if track is not None:
        index = _current_row(track, state.position, window)
        sample = track.samples[index]
        scene = replay_scene(track, state.position, index, threshold, cutoff)
    else:
        scene = TEACHING[teaching]
    review_scope = (selection, state.position, index, threshold, teaching, scene, inspection, window)
    can_review = renderer == WEBGL and bool(scene.parts) and not (state.playing or inspection.exploded)
    reviewing = can_review and st.session_state.get(REVIEW_KEY) == review_scope
    if not reviewing:
        st.session_state.pop(REVIEW_KEY, None)
    related_review = scene.parts if reviewing else ()
    phase = 0 if scene is GAP else illustration_phase(state.position)
    export_context = None
    if renderer == WEBGL and not state.playing:
        export_context = inspection_context(
            sample=sample, index=index, total=len(track.samples) if track is not None else 0,
            threshold=threshold, scene=scene, teaching=teaching, fictional=source == DEMO,
            batch_id=selection[-1] if track is not None else None,
        )
    model, panel = st.columns([5.2, 1.8], gap="small")
    with model, st.container(border=True, key="bogie_model_card"):
        st.caption("模型视窗　/　拖动旋转 · 滚轮缩放 · " + ("单击检查 · 双击聚焦" if renderer == WEBGL else "悬停查看名称"))
        if inspection.exploded:
            st.caption("分解示意 · 构件间距仅用于看清安装位置。")
        compare = inspection.comparison and bool(scene.parts) and not inspection.exploded
        link_group = camera_group if compare else None
        reference_row = None
        bounds = {"first": window.first_row, "last": window.last_row} if window else {}
        if compare and track is not None and not state.playing and scene is not GAP:
            reference_row = comparison_reference(track, index, threshold, cutoff, speed_tolerance=speed_tolerance,
                                                 **bounds)
        if not inspection.visible and not reviewing:
            st.info("请至少选择一个部件。回放和读值仍会更新。")
        else:
            try:
                if compare:
                    active, reference = st.columns(2)
                    active.caption("当前异常样本 · 示意姿态" if track is not None else "当前教学场景")
                    if reference_row is not None:
                        reference.caption("同批次未超阈值样本 · 示意姿态")
                    else:
                        reference.caption("同一相位 · 正常运动示意（无对照读数）")
                else:
                    active = st.container()
                height = 390 if compare else (720 if renderer == WEBGL else 500)
                with active:
                    _model_view(scene.mode, phase, state, inspection, scene.parts, height, "bogie_model",
                                renderer, scene is not GAP, labels=inspection.labels, focus=inspection.focus,
                                exploded=inspection.exploded, export_context=export_context, periodic=track is None,
                                related_review=related_review, camera_group=link_group)
                if compare:
                    with reference:
                        position = track.offsets[reference_row] if reference_row is not None else state.position
                        ref_state = ReplayState(position=position, rate=state.rate) if reference_row is not None else state
                        reference_context = None
                        if reference_row is not None and renderer == WEBGL:
                            reference_context = inspection_context(
                                sample=track.samples[reference_row], index=reference_row, total=len(track.samples),
                                threshold=threshold, scene=replay_scene(track, position, reference_row, threshold, cutoff),
                                teaching=teaching, fictional=source == DEMO, batch_id=selection[-1], reference=True,
                            )
                        _model_view("正常运行", illustration_phase(position), ref_state, inspection, (), height,
                                    "bogie_reference", renderer, scene is not GAP, reference=True,
                                    export_context=reference_context,
                                    periodic=track is None, related_review=related_review, camera_group=link_group)
                if reference_row is not None:
                    ref_sample = track.samples[reference_row]
                    st.table([{
                        "样本": label, "批次行": row + 1, "时间（UTC）": values["timestamp"],
                        "速度（km/h）": f"{values['speed_kmh']:.1f}",
                        "横向（m/s²）": f"{values['lateral_accel']:.3f}",
                        "垂向（m/s²）": f"{values['vertical_accel']:.3f}",
                    } for label, row, values in (("当前异常", index, sample), ("未超阈值", reference_row, ref_sample))])
                    speed_difference = abs(sample["speed_kmh"] - ref_sample["speed_kmh"])
                    st.caption(f"两条样本的速度差：{speed_difference:.2f} km/h。"
                               "同一采样段内优先选择时间最近的符合条件样本；未超阈值不代表车辆安全，模型姿态为示意。")
                    fictional = source == DEMO
                    st.download_button(
                        "下载对照证据 CSV",
                        comparison_evidence_csv(track, index, reference_row, batch_id=selection[-1],
                                                vehicle_id=None if fictional else selection[1], fictional=fictional,
                                                threshold=threshold, cutoff=cutoff, speed_tolerance=speed_tolerance,
                                                **bounds),
                        file_name=(f"railvehicle-comparison-{'demo' if fictional else 'batch'}-"
                                   f"{abs(selection[-1])}-{index + 1}-{reference_row + 1}.csv"),
                        mime="text/csv", on_click="ignore", key="bogie_comparison_download",
                        help="仅导出这两条观测、批次行号、阈值与筛选范围，不包含模型姿态。",
                    )
                    if WORKBENCH_RETURN in st.session_state:
                        attachment = ReportComparison.capture(
                            track, index, reference_row, vehicle_id=None if fictional else selection[1],
                            batch_id=selection[-1], fictional=fictional, threshold=threshold,
                            speed_tolerance=speed_tolerance,
                        )
                        if st.button("带着对照返回分析", key="bogie_comparison_to_report",
                                     help="两条观测经原分析范围重新核对后，可附在本机 PDF 报告中。"):
                            return_to_workbench(attachment)
                            st.rerun(scope="app")
                elif compare and track is not None:
                    if state.playing:
                        st.caption("暂停后查找附近的同批次样本。播放中仅显示正常运动示意。")
                    elif speed_tolerance is not None:
                        st.caption(f"附近没有同时满足未超阈值及速度差 ≤ {speed_tolerance:g} km/h 的参照样本。"
                                   "可调整速度差上限；当前不提供参照读数。")
                    else:
                        st.caption("附近没有符合条件的未超阈值样本；当前仅显示正常运动示意。")
                elif inspection.comparison and track is not None:
                    st.caption("定位到超阈值样本后显示对照。")
            except (TypeError, ValueError, RuntimeError):
                st.error("三维视图暂时无法加载，仍可查看运行信号。")
    with panel, st.container(border=True, key="bogie_status_card"):
        if track is not None:
            st.subheader("观测读数")
            if scene is GAP:
                st.caption("间隔前最后一次观测")
            date, _, clock = sample["timestamp"].partition(" ")  # ReplayTrack stores normalized UTC.
            st.caption(f"{date} UTC  \n{clock.removesuffix('+00:00')} · 第 {index + 1:,}/{len(track.samples):,} 条")
            st.metric("速度（km/h）", f"{sample['speed_kmh']:.1f}")
            st.metric("横向加速度（m/s²）", f"{sample['lateral_accel']:.3f}")
            st.metric("垂向加速度（m/s²）", f"{sample['vertical_accel']:.3f}")
        st.subheader("规则状态" if track is not None else "教学场景")
        if scene is GAP or (track is not None and scene.parts):
            st.warning(scene.label)
        else:
            st.write(scene.label if track is not None else teaching)
        st.write("示意关联构件：" + ("、".join(scene.parts) or "无"))
        if scene.parts and renderer == WEBGL:
            if st.button("返回原检查视图" if reviewing else "查看关联构件", key="bogie_related_review_action",
                         disabled=not can_review, width="stretch",
                         help="暂停且退出拆解后可用。保持当前观测和阈值不变。"):
                if reviewing:
                    st.session_state.pop(REVIEW_KEY, None)
                else:
                    st.session_state[REVIEW_KEY] = review_scope
                st.rerun()
            if reviewing:
                st.caption("当前只显示示意关联构件，用于观察结构位置。")
        if track is not None:
            st.caption(scene.impact)
            st.write("建议：" + scene.check)
        elif scene.parts:
            st.caption("教学高亮用于观察构件位置，不代表检测到故障。")
        if scene.parts:
            st.caption("琥珀色表示与当前场景关联的部件。")
            hidden = [part for part in scene.parts if part not in (related_review or inspection.visible)]
            if hidden:
                st.caption("当前未显示：" + "、".join(hidden))
        if inspection.focus and not reviewing:
            st.caption(f"部件说明 · {inspection.focus}：{PART_DESCRIPTIONS[inspection.focus]}")
            if inspection.focus not in inspection.visible:
                st.caption("该部件已隐藏，可在左侧重新显示。")
            elif inspection.focus not in scene.parts:
                st.caption(("蓝色" if renderer == WEBGL else "青色") + "表示手动选择的部件。")
        if window and window.timed:
            elapsed = max(0., state.position - window.start)
            span = window.end - window.start
            st.progress(min(1., elapsed/span), text=f"循环进度：{elapsed:.3f} / {span:.3f} 秒")
        elif window:
            current, count = index - window.first_row + 1, window.last_row - window.first_row + 1
            st.progress(current / count, text=f"核查样本：{current} / {count}")
        else:
            st.progress(state.position / duration if duration else 1.0,
                        text=f"{state.position:.2f} / {duration:.2f} 秒")
    if track is not None and show_signals:
        with st.container(border=True, key="bogie_signal_card"):
            st.subheader("运行信号")
            st.caption("青色线为当前观测；红色菱形为超阈值样本。两次采样之间不补算数据。")
            chart = build_replay_chart(track, index, state.position, threshold, event_index=event_index,
                                       playhead=True, window=window)
            plot_chart(chart, width="stretch", key="bogie_curves")


def _control_playback(state, action, now, track, window, *, rate=None):
    if window:
        return control_event_loop(state, action, now, window, rate=rate)
    if track is None:
        return control_teaching(state, action, now, rate=rate)
    return control_replay(state, action, now, track.duration, rate=rate)


def render():
    page_title("三维诊断工作台", "通用双轴转向架 · 观测读值、规则超阈值与模型示意分别显示。")
    render_replay_return()
    st.html("""<style>
      .st-key-bogie_model_card {background:var(--rv-surface,#101f31);padding:.55rem;}
      .st-key-bogie_model_card [data-testid=stVerticalBlock] {gap:.35rem;}
      .st-key-bogie_status_card [data-testid=stMetricValue] {font-variant-numeric:tabular-nums;font-size:1.4rem;}
      .st-key-bogie_status_card [data-testid=stVerticalBlock] {gap:.55rem;}
    </style>""")
    target = st.session_state.pop("bogie_evidence_target", None)
    batch_target = st.session_state.pop("bogie_batch_target", None)
    requested_scope = (target or batch_target or {}).get("scope")
    if STATE_KEY not in st.session_state:
        _clear_loop()
    choices = [TEACH, *demo_source_options(HISTORY, DEMO)]
    candidate = target or batch_target
    fictional = bool(candidate and candidate.get("fictional") is True)
    requested = candidate if fictional or HISTORY in choices else None
    if requested:
        _clear_loop(pause=True)
        st.session_state["bogie_source"] = DEMO if fictional else HISTORY
    if requested and "threshold" in requested:
        st.session_state["bogie_threshold"] = requested["threshold"]
    options, main = st.columns([1.18, 5.82], gap="small")
    with options:
        st.subheader("场景与结构")
        source = st.radio("数据来源", choices, key="bogie_source")
        teaching = st.selectbox("教学场景", list(TEACHING), key="bogie_teaching") if source == TEACH else next(iter(TEACHING))
        if source == DEMO:
            st.caption("虚构数据，非实际测量。")
        elif source == HISTORY:
            st.caption("历史导入样本；模型运动为示意，不用于反推位移、轮轨力或故障。")
        selection = (source,)
        try:
            selection, load = _load(source, requested)
            if selection != st.session_state.get("bogie_selection") or (requested and (target or requested_scope)):
                track = load() if load else None
                st.session_state["bogie_track"] = track
                st.session_state["bogie_gap_cutoff"] = gap_cutoff(track) if track else None
                st.session_state["bogie_selection"] = selection
                st.session_state.pop("bogie_events", None)
                _clear_loop()
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
        if requested and batch_target and not target and batch_target["batch_id"] is not None:
            expected_batch = ((DEMO, batch_target["batch_id"]) if fictional else
                              (HISTORY, batch_target["vehicle_id"], batch_target["batch_id"]))
            if track is None or selection != expected_batch:
                st.warning("未能打开入口指定的批次，请核对车辆和批次。")
            elif requested_scope is not None:
                if isinstance(requested_scope, ReplayEvidenceScope) and requested_scope.matches_track(track):
                    first, last = requested_scope.first_row, requested_scope.last_row
                    _open_window(track, threshold,
                                 EventLoop("所选分析范围", track.offsets[first], track.offsets[last], first, last))
                else:
                    st.warning("分析窗口与当前批次的观测记录不匹配，请回到运行分析重新选择。")
        if source == DEMO and track is not None:
            st.button("核查虚构证据 →", key="bogie_demo_evidence", on_click=navigate,
                      args=("AI 智能分析中心",), kwargs={
                          "ai_center_demo": True, "ai_demo_batch": selection[1],
                          "ai_demo_threshold": threshold,
                      })
        comparison = st.toggle("对照未超阈值样本" if source != TEACH else "对照正常运动",
                               key="bogie_compare", disabled=source != TEACH and track is None,
                               help="暂停在异常样本后，查找同一采样段内相距不超过 10 秒的未超阈值样本。")
        with st.expander("视角与部件"):
            renderer = st.selectbox("渲染方式", [WEBGL, COMPAT] if webgl_available() else [COMPAT],
                                    key="bogie_renderer")
            inspection = _inspection_controls(source, comparison)
        camera_group = speed_tolerance = None
        if comparison and track is not None:
            speed_tolerance = st.selectbox("参照速度差上限", [None, 1.0, 3.0, 5.0, 10.0], index=3,
                                          format_func=lambda value: "不限速度" if value is None else f"{value:g} km/h",
                                          key="bogie_compare_speed",
                                          help="与当前异常样本比较。找不到符合条件的样本时，不会自动放宽范围。")
        if comparison and renderer == WEBGL:
            if st.toggle("联动两侧镜头", value=True, key="bogie_link_cameras",
                         help="同步旋转、平移和缩放。零件选择、隐藏和剖切仍各自独立。"):
                if "bogie_camera_pair" not in st.session_state:
                    st.session_state["bogie_camera_pair"] = token_urlsafe(18)
                camera_group = st.session_state["bogie_camera_pair"]
    if target and requested:
        row = None
        expected = ((DEMO, target["batch_id"]) if fictional else
                    (HISTORY, target["vehicle_id"], target["batch_id"]))
        scope_matches = (requested_scope is None or (track is not None
                         and isinstance(requested_scope, ReplayEvidenceScope) and requested_scope.matches_track(track)))
        if track is not None and selection == expected and scope_matches:
            try:
                row = resolve_event_row(track, target["timestamp"], target["axis"], threshold,
                                        row_span=target.get("row_span"), anchor=target.get("anchor"))
            except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
                row = None
        if row is not None and requested_scope is not None:
            first, last = requested_scope.first_row, requested_scope.last_row
            if first <= row <= last:
                _open_window(track, threshold,
                             EventLoop("所选分析范围", track.offsets[first], track.offsets[last], first, last))
            else:
                row = None
        if row is not None:
            _seek(track.offsets[row], row)
            st.success("已定位到虚构演示的异常样本。" if fictional else
                       "已定位到所选统计证据的异常样本。")
        else:
            st.warning("无法唯一定位对应的异常样本。数据可能已变化，或旧证据包含同一时刻的多个候选；"
                       "请回到运行分析重新核查证据。")
    active_loop = st.session_state.get(LOOP_KEY)
    if active_loop and (active_loop["track"] is not track or active_loop["threshold"] != threshold):
        _clear_loop(pause=active_loop["track"] is track)
        active_loop = None
        st.caption("数据或阈值已变化，已退出原异常区段窗口。")
    window = active_loop["window"] if active_loop else None
    static_window = window is not None and not window.timed
    with main:
        state = st.session_state.get(STATE_KEY, ReplayState())
        duration = track.duration if track is not None else ILLUSTRATION_PERIOD
        transport = st.container()
        view_slot = st.container()
        if inspection.exploded and state.playing:
            state = _control_playback(state, "pause", monotonic(), track, window)
        if source != TEACH and track is None:
            st.caption("暂无可回放的样本；仍可观察教学模型。")
        if window:
            status, exit_loop = st.columns([3, 1], vertical_alignment="center")
            if static_window:
                status.info(f"逐帧窗口：{window.label} · 同一时刻 · 第 {window.first_row+1:,}–{window.last_row+1:,} 条")
            else:
                status.info(f"循环窗口：{window.label} · 批次相对时间 {window.start:.6g}–{window.end:.6g} 秒")
            exit_loop.button("退出逐帧核查" if static_window else "退出循环", key="bogie_exit_loop",
                             on_click=_clear_loop, kwargs={"pause": True})
            _window_evidence(track, window, selection, threshold)
        with transport, st.container(horizontal=True, vertical_alignment="bottom"):
            action = None
            if st.button("播放", key="bogie_play", icon=":material/play_arrow:", type="primary",
                         disabled=static_window or inspection.exploded or state.playing or duration == 0):
                action = "play"
            if st.button("暂停", key="bogie_pause", icon=":material/pause:", disabled=not state.playing):
                action = "pause"
            if st.button("重置", key="bogie_reset", icon=":material/restart_alt:",
                         help="返回当前回放窗口的首条观测。" if window else "返回当前回放起点。"):
                action = "reset"
            rate = st.selectbox("播放速度", [0.5, 1., 2., 4.], index=1, width=110, key="bogie_rate",
                                disabled=static_window, format_func=lambda x: f"{x:g}×")
        if rate != state.rate:
            state = _control_playback(state, "rate", monotonic(), track, window, rate=rate)
        if action:
            state = _control_playback(state, action, monotonic(), track, window)
            if action in ("play", "reset"):
                st.session_state[FRAME_KEY] = None
        st.session_state[STATE_KEY] = state
        if action:
            st.rerun()
        if track is not None:
            if static_window:
                st.caption(f"批次相对时间 {window.start:.6g} 秒；用上一帧或下一帧按样本行核查。")
            else:
                start, end = (window.start, window.end) if window else (0., max(duration, 0.001))
                precision = dict(step=min(.01, (end-start)/100), format="%.6f") if window else {}
                st.session_state["bogie_seek"] = state.position
                st.slider("时间轴定位（秒）", start, end, key="bogie_seek", **precision,
                          disabled=state.playing or duration == 0,
                          on_change=lambda: _seek(st.session_state["bogie_seek"]))
            row = _current_row(track, state.position, window)
            first_row, last_row = (window.first_row, window.last_row) if window else (0, len(track.samples)-1)
            prev, nxt = st.columns(2)
            for column, label, step in ((prev, "上一帧", -1), (nxt, "下一帧", 1)):
                i = row + step
                if column.button(label, disabled=state.playing or not first_row <= i <= last_row):
                    _seek(track.offsets[i], i)
                    st.rerun()
        event_index = None
        show_signals = False
        if track is not None:
            show_signals = st.toggle("显示同步运行曲线", value=True, key="bogie_show_signals",
                                     help="关闭曲线可减少重绘；三维模型、异常提示和当前样本读数继续更新。")
        if track is not None:
            cache = st.session_state.get("bogie_events")
            if (not isinstance(cache, dict) or cache.get("track") is not track
                    or cache.get("threshold") != threshold or "catalog" not in cache):
                events = event_positions(track, threshold)
                event_index = EventSpanIndex.from_events(events)
                cache = dict(track=track, threshold=threshold, index=event_index,
                             catalog=replay_event_catalog(track, events, event_index))
                st.session_state["bogie_events"] = cache
                for direction in EVENT_FILTERS:
                    st.session_state.pop(f"bogie_event_page_{direction}", None)
                    st.session_state.pop(f"bogie_event_choice_{direction}", None)
            event_index = cache["index"]
            _event_explorer(track, cache, threshold, state, window)
        with view_slot:
            st.fragment(run_every=(0.15 if track is None else 0.4) if state.playing else None)(_display)(
                track, duration, threshold, st.session_state.get("bogie_gap_cutoff"),
                teaching, inspection, event_index, show_signals, window, renderer, source,
                selection, camera_group, speed_tolerance)
