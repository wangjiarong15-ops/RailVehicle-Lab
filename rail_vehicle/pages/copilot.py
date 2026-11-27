"""Engineering chat workspace. Streamlit owns interactions, workers own bounded computations."""

from dataclasses import replace
import json
import sqlite3

import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.ui import render_validated_result
from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_inspection_context import inspection_context
from rail_vehicle.bogie_replay import TEACHING, illustration_phase, scene_for_sample
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.copilot.archive import export_chat, read_chat
from rail_vehicle.copilot.context import Dataset, MAX_ROWS, SOURCE_LABELS, csv_datasets
from rail_vehicle.copilot.conversation import Conversation, Job, prior_calls
from rail_vehicle.copilot.knowledge import document_passages
from rail_vehicle.copilot.tools import TOOLS, Work
from rail_vehicle.demo_data import analysis_demo_data
from rail_vehicle.event_anchor import resolve_event_row
from rail_vehicle.pages.bogie_link import event_replay_button
from rail_vehicle.pages.sensors import evidence_panel, read_snapshot
from rail_vehicle.sensor_evidence import viewer_sensors
from rail_vehicle.sensor_store import active_channels
from rail_vehicle.replay import ReplayState, prepare_replay
from rail_vehicle.run_data import list_batch_samples, list_vehicle_batches
from rail_vehicle.ui import page_title, plot_chart
from rail_vehicle.vehicle_data import list_vehicles


QUICK = (
    ("横向异常与三维回查", "帮我分析当前车辆的横向振动异常，并用三维模型演示可能的原因。", "monitoring"),
    ("一系悬挂结构", "展示转向架的一系悬挂结构。", "view_in_ar"),
    ("刚度提高 20%", "将悬挂刚度提高20%，比较振动响应。", "tune"),
    ("生成工程报告", "根据本次分析生成完整报告。", "picture_as_pdf"),
)


def _attachment(label, kinds, key, limit):
    def clear_removed():
        if st.session_state.get(key) is None:
            st.session_state.pop(key + "_saved", None)

    upload = st.file_uploader(label, type=kinds, key=key, on_change=clear_removed, max_upload_size=2)
    if upload and upload.size > limit:
        raise ValueError(f"附件超过 {limit:,} 字节上限。")
    if upload is not None:
        st.session_state[key + "_saved"] = (upload.name, upload.getvalue())
    saved = st.session_state.get(key + "_saved")
    if saved and upload is None:
        st.text("已连接 " + saved[0])
        if st.button("移除此附件", key=key + "_forget"):
            st.session_state.pop(key + "_saved", None)
            return None
    return saved


def interrupt():
    running = st.session_state.pop("cp_running", None)
    if running:
        _, turn, job = running
        job.cancel.set()
        job.drain(turn)
        if turn.status == "running":
            turn.status, turn.message, turn.active = "stopped", "任务已停止。", ""


def _source(public):
    s = st.session_state
    handoff = s.get("cp_handoff")
    options = ["自由问答", "内置虚构批次"]
    if not public:
        options.extend(["历史导入批次", "会话 CSV"])
    if handoff and (not public or handoff["target"]["fictional"]):
        options.insert(1, "当前分析窗口")
    if s.get("cp_source") not in options:
        s["cp_source"] = options[0]
    source = st.selectbox("数据来源", options, key="cp_source")
    if source == "自由问答":
        return None, None
    if source == "当前分析窗口":
        v, b, rows = handoff["copilot_data"]
        sel = handoff["evidence_selection"]
        origin = "demo" if handoff["target"]["fictional"] else "history"
        return Dataset(
            origin,
            v,
            b,
            rows,
            sel["threshold"],
            sel.get("row_span"),
            handoff["target"].get("scope"),
            preferred_event=handoff.get("evidence_id"),
            sensors=read_snapshot(v["id"], b["id"]) if origin == "history" else {},
        ), None
    threshold = st.number_input(
        "加速度绝对值阈值（m/s²）", min_value=0.0, value=s.get("cp_threshold", 1.0),
        step=0.1, key="cp_threshold_input",
    )
    s["cp_threshold"] = threshold
    if threshold is None:
        raise ValueError("请输入加速度阈值。")
    if source == "会话 CSV":
        upload = _attachment("临时分析 CSV", ["csv"], "cp_csv", 2_000_000)
        if not upload:
            st.caption("上传 UTF-8 CSV 后可按车辆分别分析。附件仅在本次会话中使用。")
            return None, None
        sets = csv_datasets(upload[1], upload[0], threshold)
        chosen = st.selectbox("附件车辆", list(sets), key="cp_attachment_vehicle")
        return sets[chosen], None
    demo = source == "内置虚构批次"
    if demo:
        v, batches, rows = analysis_demo_data()
    else:
        vehicles = {v["id"]: v for v in list_vehicles()}
        if not vehicles:
            st.info("暂无历史车辆，可选择内置虚构批次体验。")
            return None, None
        v = vehicles[
            st.selectbox(
                "车辆", list(vehicles), format_func=lambda i: vehicles[i]["vehicle_code"], key="cp_vehicle"
            )
        ]
        batches = list_vehicle_batches(v["id"])
    if not batches:
        st.info("这辆车还没有导入批次。")
        return None, None
    by_id = {b["id"]: b for b in batches}
    key = f"{source}_{v['id']}"
    label = lambda i: by_id[i]["file_name"]
    chosen = st.selectbox("分析批次", list(by_id), format_func=label, key="cp_batch_" + key)
    other = st.selectbox(
        "对照批次",
        [None, *[i for i in by_id if i != chosen]],
        key="cp_other_" + key,
        format_func=lambda i: "未选择" if i is None else label(i),
    )

    def snapshot(i):
        if by_id[i]["sample_count"] > MAX_ROWS:
            raise ValueError("此批次超过 Copilot 单次样本上限，请在运行分析中缩小时间范围后进入。")
        return Dataset(
            "demo" if demo else "history",
            v,
            by_id[i],
            rows[i] if demo else list_batch_samples(v["id"], i),
            threshold,
            batches=tuple(batches),
            sensors=read_snapshot(v["id"], i) if not demo else {},
        )

    return snapshot(chosen), snapshot(other) if other is not None else None


def _plot(p, key):
    fig = go.Figure()
    for name, values in p["columns"].items():
        fig.add_scatter(x=p["x"], y=values, name=name, mode="lines")
    fig.update_layout(
        height=245,
        margin=dict(l=5, r=10, b=5, t=10),
        xaxis_title=p["xlabel"],
        yaxis_title=p["ylabel"],
        legend=dict(orientation="h", y=1.02),
    )
    plot_chart(fig, key=key, width="stretch")


def _wave(output, data, key):
    ids = {e["evidence_id"]: e for e in output.events}
    initial = list(ids).index(data.preferred_event) if data.preferred_event in ids else 0
    eid = st.selectbox("定位异常区段", list(ids), index=initial, key=key + "_event")
    e = ids[eid]
    track = prepare_replay(data.rows)
    row = resolve_event_row(track, e["start_timestamp"], e["type"], data.threshold, anchor=e["anchor"])
    if row is None:
        st.warning("此事件在当前样本中无法唯一定位。")
        return
    end = row + e["sample_count"] - 1
    lo, hi = max(0, row - 12), min(len(track.samples), end + 13)
    cursor = (
        st.slider("同步样本", lo + 1, hi, row + 1, key=key + "_" + eid + "_cursor") - 1
        if hi - lo > 1
        else lo
    )
    sample = track.samples[cursor]
    field = e["type"] + "_accel"
    y = [r[field] for r in track.samples[lo:hi]]
    fig = go.Figure(go.Scatter(x=list(range(lo + 1, hi + 1)), y=y, mode="lines+markers", name="加速度"))
    fig.add_vrect(x0=row + 0.5, x1=end + 1.5, fillcolor="#e59e51", opacity=0.22, line_width=0)
    fig.add_vline(x=cursor + 1, line_color="#39bfae", line_width=1)
    for limit in (-data.threshold, data.threshold):
        fig.add_hline(y=limit, line_dash="dot", line_color="#db9a59")
    fig.update_layout(
        height=280,
        xaxis_title="当前范围内的样本序号",
        yaxis_title="加速度（m/s²）",
        margin=dict(l=5, r=10, t=10, b=5),
    )
    waveform, model = st.columns([1, 1.2], gap="small")
    with waveform:
        plot_chart(fig, key=key + "_wave", width="stretch")
        st.caption(f"样本 {cursor + 1} · {sample['timestamp']} · {sample[field]:g} m/s²")
    with model:
        rule = scene_for_sample(sample, data.threshold)
        st.caption("规则状态 · " + rule.label + "　|　结构运动为示意")
        if webgl_available():
            render_webgl(
                mode=rule.mode,
                phase=illustration_phase(track.offsets[cursor]),
                state=ReplayState(),
                visible=PARTS,
                highlight=rule.parts,
                view="整体视图",
                focus=None,
                labels=False,
                rails=True,
                exploded=False,
                spread=0,
                continuous=True,
                periodic=False,
                camera_revision=0,
                height=330,
                key=key + "_observed",
                sensors=viewer_sensors(data.sensors, field),
                export_context=inspection_context(
                    sample=sample, index=cursor, total=len(track.samples), threshold=data.threshold,
                    scene=rule, teaching="", fictional=data.source == "demo", batch_id=data.batch["id"],
                    attachment=data.source == "upload", scoped=True,
                ),
            )
        else:
            st.info("本机三维资源不可用，仍可按样本核查波形。")
    if data.source in ("history", "upload"):
        with st.expander("异常样本与传感器证据"):
            snapshot = data.sensors or dict(vehicle_code=data.vehicle.get("vehicle_code"),
                                             file_name=data.batch["file_name"], source_sha256=data.batch.get("source_sha256"))
            evidence_panel(snapshot, field, sample, data.threshold)
            st.caption(f"规则证据 {eid} · 当前范围样本 {row+1}–{end+1} · CSV 物理行按时间顺序列出")
            st.json({"event_id": eid, "csv_lines": [s.get("csv_line") for s in track.samples[row:end+1]]}, expanded=False)
    st.caption(
        f"{eid} · {e['start_timestamp']} — {e['end_timestamp']} · 峰值 {e['maximum_absolute_value']:g} m/s²"
    )
    if data.source in ("demo", "history"):
        event_replay_button(
            label="在三维时间轴回放此区段",
            key=key + "_replay",
            vehicle_id=data.vehicle["id"],
            batch_id=data.batch["id"],
            threshold=data.threshold,
            timestamp=e["start_timestamp"],
            axis=e["type"],
            fictional=data.source == "demo",
            row_span=data.row_span,
            anchor=e["anchor"],
            scope=data.replay_scope,
        )


def _scene(scene, key, command_id, seek):
    if not webgl_available():
        st.info("三维资源不可用，计算与数值曲线仍可查看。")
        return
    simulation = scene.get("simulation")
    mode = TEACHING[scene.get("motion", "正常运行")]
    group, display = scene.get("group", "整架"), scene.get("display", "solid")
    visible = [group] if display == "isolate" else [p for p in PARTS if display != "hide" or p != group]
    t = scene.get("transport", {})
    # The response player owns its own slider, clock and pause button.
    playing = not simulation and (
        t.get("action") == "play" if t else scene.get("motion", "正常运行") != "正常运行"
    )
    secs = seek["seconds"] if seek else 0
    st.caption(
        "简化动力学计算 · 输入为教学参数" if simulation else "教学结构与运动示意 · 高亮区域用于观察装配关系"
    )
    render_webgl(
        mode=mode.mode,
        phase=illustration_phase(secs),
        state=ReplayState(position=secs, playing=playing),
        visible=visible,
        highlight=mode.parts if group == "整架" else (group,),
        view=scene.get("view", "整体视图"),
        focus=group if group != "整架" and display != "hide" else None,
        labels=display == "explode",
        rails=display != "explode",
        exploded=display == "explode",
        spread=1,
        continuous=True,
        periodic=True,
        camera_revision=0,
        height=470,
        key=key,
        simulation=simulation,
        studio_command=dict(
            id=command_id, surface=display if display in ("ghost", "wire") else "solid",
            transport=t, seek=seek,
        ),
    )


def _turn(turn, work, detail, live_keys):
    with st.chat_message("user"):
        st.text(turn.prompt)
    with st.chat_message("assistant"):
        st.caption(turn.mode + " · " + (SOURCE_LABELS[work.data.source] if work.data else "自由问答"))
        has_observations = any(r["result"].events for r in turn.results)
        for i, r in enumerate(turn.results):
            o = r["result"]
            st.text(o.text)
            if o.refs:
                st.caption("证据　" + " · ".join(o.refs))
            if not detail:
                continue
            key = f"cp_{turn.id}_{i}"
            if o.provenance:
                st.download_button("下载计算证据与配置版本", json.dumps(o.provenance, ensure_ascii=False, indent=2),
                                   file_name="channel-evidence.json", mime="application/json", key=key+"_provenance")
            if o.rows:
                st.dataframe(o.rows, hide_index=True)
            if r["tool"] == "sensors" and active_channels(work.data.sensors) and webgl_available():
                bindings = active_channels(work.data.sensors)
                field = st.selectbox("查看登记安装点", list(bindings), key=key+"_sensor",
                                     format_func=lambda f: bindings[f]["serial"]+" · "+f)
                render_webgl(mode="正常运行", phase=0, state=ReplayState(), visible=PARTS, highlight=(),
                             view="整体视图", focus=None, labels=False, rails=True, exploded=False, spread=0,
                             camera_revision=0, continuous=False, height=420, key=key+"_sensor_model",
                             sensors=viewer_sensors(work.data.sensors, field))
            if o.events:
                _wave(o, work.data, key)
            if o.plot:
                p = o.plot
                if (
                    "fft" in p
                    and st.radio("频谱", ["PSD", "FFT"], horizontal=True, key=key + "_spectrum") == "FFT"
                ):
                    p = p["fft"]
                _plot(p, key + "_chart")
            if o.citations:
                with st.expander("检索资料与出处", expanded=True):
                    for c in o.citations:
                        st.text(c["title"] + " · " + c["id"])
                        st.text(c["text"])
                        st.text(c["source"])
            if o.scene and key not in live_keys:
                seconds = o.scene.get("transport", {}).get("seconds")
                if r["tool"] != "transport" or seconds is not None:
                    if not has_observations or st.toggle("查看独立教学演示", key=key + "_show_teaching"):
                        seek = {"id": key, "seconds": seconds} if seconds is not None else None
                        _scene(o.scene, key + "_model", key, seek)
            if o.pdf:
                st.download_button(
                    "下载工程分析 PDF",
                    o.pdf,
                    "railvehicle-analysis.pdf",
                    "application/pdf",
                    key=key + "_pdf",
                )
        if turn.verified:
            with st.expander("AI 辅助解读 · 已核对证据范围", expanded=detail):
                render_validated_result(turn.verified, work.anonymous_payload, st)
                for p in turn.verified.get("knowledge_citations", []):
                    st.text(f"{p['id']} · {p['title']} · {p['source']}")
        if turn.results:
            with st.expander(f"工具记录 · 已完成 {len(turn.results)} 步", expanded=False):
                st.dataframe(
                    [
                        {
                            "工具": TOOLS[r["tool"]].label,
                            "参数": json.dumps(r["args"], ensure_ascii=False),
                            "耗时（ms）": r["ms"],
                        }
                        for r in turn.results
                    ],
                    hide_index=True,
                )
        if turn.message:
            st.warning(turn.message)


@st.fragment(run_every=0.35)
def _progress(running):
    _, turn, job = running
    job.drain(turn)
    if turn.status != "running":
        st.session_state.pop("cp_running", None)
        st.rerun()
    status = st.status(
        "正在执行 · "
        + (TOOLS[turn.active].label if turn.active in TOOLS else turn.active or "准备工具计划"),
        expanded=True,
    )
    for r in turn.results:
        status.text(TOOLS[r["tool"]].label + "　" + r["result"].text)
    for text, eid in turn.streaming:
        status.text(text + " [" + eid + "]")
    if st.button("停止生成", key="cp_stop"):
        interrupt()
        st.rerun()


def render(public):
    page_title("RailVehicle Copilot")
    s = st.session_state
    for key, value in s.get("cp_saved_source_controls", {}).items():
        if key not in s:
            s[key] = value
    bar = st.columns(4, gap="small")
    other = None
    source_error = None
    with bar[0].popover("数据来源与附件", icon=":material/database:", width="stretch"):
        try:
            data, other = _source(public)
        except (ValueError, sqlite3.Error, OSError) as exc:
            source_error = str(exc) if isinstance(exc, ValueError) else "本机数据暂时无法读取。"
            data = None
        notes = ()
        if not public:
            try:
                upload = _attachment("已获授权的工程资料", ["md", "pdf"], "cp_notes", 2_000_000)
                if upload:
                    cache = s.get("cp_document")
                    if not cache or cache[0] != upload:
                        s["cp_document"] = (upload, document_passages(upload[1], upload[0]))
                    notes = s["cp_document"][1]
                    st.caption(f"已索引 {len(notes)} 段资料。")
                else:
                    s.pop("cp_document", None)
            except ValueError as exc:
                s.pop("cp_document", None)
                st.warning(str(exc))
    s["cp_saved_source_controls"] = {
        k: v
        for k, v in s.items()
        if k in ("cp_source", "cp_threshold", "cp_vehicle", "cp_attachment_vehicle")
        or k.startswith(("cp_batch_", "cp_other_"))
    }
    if source_error:
        st.warning(source_error)
    if data is None:
        s.pop("cp_bound_batch", None)
    else:
        s["cp_bound_batch"] = (data.source, data.vehicle["id"], data.batch["id"], data.threshold)
    scope = (data.identity if data else "unbound", other.identity if other else "", tuple((p.id, p.title) for p in notes))
    pool = s.setdefault("cp_threads", [])
    active = next((c for c in pool if c.id == s.get("cp_active") and c.scope == scope), None)
    if active is None:
        interrupt()
        active = next((c for c in reversed(pool) if c.scope == scope), None) or Conversation(scope)
        if active not in pool:
            pool.append(active)
        s["cp_active"] = active.id
    if bar[3].button("新建会话", icon=":material/add:", width="stretch", key="cp_new"):
        interrupt()
        active = Conversation(scope)
        pool.append(active)
        s["cp_active"] = active.id
        st.rerun()
    restore_prompt = None
    with bar[1].popover("会话记录", icon=":material/forum:", width="stretch"):
        st.caption("当前浏览器会话保留最近 10 个对话，每个对话最多 12 轮。可下载归档。")
        for c in reversed(pool):
            if c.scope == scope and st.button(
                (c.turns[0].prompt[:28] if c.turns else "新会话"),
                key="cp_open_" + c.id,
                disabled=c is active,
            ):
                interrupt()
                s["cp_active"] = c.id
                st.rerun()
        if active.turns:
            st.download_button(
                "保存对话记录", export_chat(active), "railvehicle-conversation.json", "application/json"
            )
        if not public:
            saved = st.file_uploader("打开已保存的会话记录", type=["json"], key="cp_archive")
            if saved:
                try:
                    past = read_chat(saved.getvalue())
                    st.caption("归档文本 · 可选择一个问题，按当前数据重新执行。")
                    for question, lines in past:
                        st.text(question)
                        for line in lines:
                            st.text(line)
                    question = st.selectbox("复算问题", [q for q, _ in past])
                    if st.button("使用当前批次复算", disabled=bool(s.get("cp_running"))):
                        restore_prompt = question
                except ValueError as exc:
                    st.warning(str(exc))
    while len(pool) > 10:
        pool.remove(next(c for c in pool if c is not active))
    config = None
    consent = interpret = share_notes = False
    with bar[2].popover("模型与权限", icon=":material/tune:", width="stretch"):
        mode = st.radio("规划方式", ["离线工具"] if public else ["离线工具", "在线模型"], key="cp_mode")
        if mode == "在线模型":
            config = AIConfig.from_sources()
            model = st.text_input("模型名称", value=config.model, max_chars=100, key="cp_model")
            config = replace(config, model=model.strip(), timeout=min(config.timeout, 20.0))
            st.caption(
                "当前服务 "
                + config.provider
                + "。在线规划发送本轮问题、引用的事件编号、前序工具选择与内置资料摘要。"
            )
        else:
            st.caption("工具在本机执行，使用规则规划，无需 API 密钥。")
    if data:
        category = SOURCE_LABELS[data.source]
        provenance = data.source_description
        source = f"{category} · {provenance}" if category != provenance else category
        st.caption(f"{source}　/　{data.vehicle['vehicle_code']}　/　{data.batch['file_name']}　·　"
                   f"{len(data.rows):,} 行　·　阈值 {data.threshold:g} m/s²")
        if data.row_span:
            st.caption(f"当前窗口 · 批次第 {data.row_span[0]:,}–{data.row_span[1]:,} 条观测 · "
                       f"{data.rows[0]['timestamp']} 至 {data.rows[-1]['timestamp']}")
        if s.get("cp_source") == "当前分析窗口":
            from rail_vehicle.pages.data_workflow import analysis_button, render_workbench_return
            from rail_vehicle.ui import REPORT
            with st.container(horizontal=True, gap="small"):
                render_workbench_return("cp_return_analysis")
                if data.source == "history" and data.replay_scope:
                    analysis_button(st, label="准备当前窗口报告", key="cp_window_report", page=REPORT,
                                    vehicle_id=data.vehicle["id"], batch_id=data.batch["id"],
                                    scope=data.replay_scope, threshold=data.threshold)
    work = Work(
        data,
        other,
        notes,
        active.last_scene(),
        active.last_verified(),
        [r for turn in active.turns for r in turn.results if r["tool"] != "report"],
    )
    running = s.get("cp_running")
    if running and running[0] != active.id:
        interrupt()
        running = None
    prompt = restore_prompt
    typed = None
    placeholder = "输入工程问题，或说明想检查的部件、数据和工况…"
    if not active.turns:
        with st.container(key="cp_composer"):
            st.subheader("自由对话")
            st.caption(f"{config.provider} · {config.model}" if config else
                       "离线工具 · 可查工程资料、观察部件或试算悬挂响应。分析运行记录时再连接批次。")
            typed = st.chat_input(placeholder, key="cp_input", max_chars=1200)
        with st.container(horizontal=True, gap="xsmall"):
            for i, (label, instruction, icon) in enumerate(QUICK):
                if not data and i in (0, 3):
                    continue
                if st.button(label, icon=f":material/{icon}:", key=f"cp_quick_{i}"):
                    prompt = instruction
            if not data:
                st.button("使用虚构批次体验", icon=":material/science:",
                          on_click=lambda: s.update(cp_source="内置虚构批次"))
        if data and st.toggle("预览所选样本的加速度", key="cp_show_preview"):
            try:
                rows = work.stats["samples"]
                _plot(dict(x=[r["timestamp"] for r in rows],
                           columns={"横向": [r["lateral_accel"] for r in rows],
                                    "垂向": [r["vertical_accel"] for r in rows]},
                           xlabel="采样时间", ylabel="m/s²"), "cp_preview")
            except (KeyError, TypeError, ValueError):
                st.warning("无法绘制预览，请检查时间、速度与加速度列。")
    # Keep the live iframe outside the changing transcript. A new scene gets a new
    # instance; transport turns target the existing camera, animation and sample cursor.
    origin = command = scene = seek = None
    optional_teaching = False
    completed = [t for t in active.turns if t.status != "running"]
    for t in completed:
        for i, result in enumerate(t.results):
            if result["result"].scene:
                command = f"cp_{t.id}_{i}"
                if result["tool"] != "transport" or origin is None:
                    origin = command
                    seek = None
                scene = result["result"].scene
                seconds = scene.get("transport", {}).get("seconds")
                if seconds is not None:
                    seek = {"id": command, "seconds": seconds}
                optional_teaching = any(r["result"].events for r in t.results)
    with st.container(key="cp_transcript"):
        for t in completed:
            detail = t is active.turns[-1] or st.toggle("展开此轮的图表与证据", key="cp_history_" + t.id)
            _turn(t, work, detail, (origin, command))
    if scene:
        with st.container(key="cp_active_viewer"):
            if not optional_teaching or st.toggle("查看独立教学演示", key=origin + "_active_teaching"):
                _scene(scene, origin + "_active_model", command, seek)
    if running:
        _progress(running)
        return
    if config is not None:
        with st.container(border=True, key="cp_turn_permissions"):
            st.caption(f"本轮将使用 {config.provider} · {config.model}")
            left, right = st.columns(2)
            consent = left.checkbox(
                "授权本轮在线规划（可能产生 API 费用）", key=f"cp_consent_{active.id}_{len(active.turns)}"
            )
            interpret = right.checkbox(
                "同时允许发送匿名聚合指标和事件时间以生成证据解读",
                key=f"cp_insight_{active.id}_{len(active.turns)}",
            )
            if notes:
                share_notes = st.checkbox(
                    "允许发送本轮匹配的本地资料摘录（每次请求最多三段）",
                    key=f"cp_share_{active.id}_{len(active.turns)}",
                )
    if active.turns and active.turns[-1].status in ("error", "stopped"):
        if st.button("重试上一轮", key="cp_retry"):
            prompt = active.turns[-1].prompt
    if active.turns:
        typed = st.chat_input(placeholder, key="cp_input", max_chars=1200)
    prompt = typed or prompt
    if prompt:
        if config is not None and (
            not consent or not config.enabled or not config.api_key or not config.model
        ):
            st.warning("请检查模型配置，并勾选本轮在线调用授权，或切换到离线工具。")
            return
        previous = prior_calls(active.turns)
        try:
            turn = active.begin(prompt, mode)
        except ValueError as exc:
            st.warning(str(exc))
            return
        job = Job()
        s["cp_running"] = (active.id, turn, job)
        job.start(
            work,
            prompt,
            previous,
            config=config,
            authorized=consent,
            interpret=interpret,
            share_notes=share_notes,
        )
        st.rerun()
