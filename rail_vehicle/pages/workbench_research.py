"""Copilot research workspace. Cloud proposals and local execution need separate actions."""

from io import BytesIO
import sqlite3
from time import time
import zlib

import streamlit as st

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.provider import AIProviderUnavailable
from rail_vehicle.copilot.planning import planning_client
from rail_vehicle.workbench_planning import COMPLETION_TOKENS, cloud_proposal, local_proposal
from rail_vehicle.workbench_queue import (LEASE, archived_round, create, dispatch, duplicate_for_replay, job, listing,
                                          note_cloud_call, record_round, recover, results, update, validate)
from rail_vehicle.workbench_tools import TOOLS, serialize

STATUS = {"draft":"待规划","queued":"排队","running":"执行中","paused":"暂停","review":"待复核",
          "error":"中断","cancelled":"已取消","complete":"已完成"}
BOUND = {"observation_analysis":"观测分析","schematic":"结构示意","published_numerical_reference":"公开数值资料",
         "simulation":"数值仿真","simulation_comparison":"数值方案对照","synthetic":"合成记录",
         "knowledge":"工程资料","evidence_summary":"证据汇总","engineering_context":"已冻结工程上下文"}


def _pdf(record, proof):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.pagesizes import A4
    from rail_vehicle.analysis_report import _font_names

    stream = BytesIO()
    page = Canvas(stream,pagesize=A4)
    normal,bold = _font_names()
    page.setFont(bold,16)
    page.drawString(36,795,"RailVehicle-Lab 工程研究记录")
    page.setFont(normal,10)
    y = 761
    lines = [record["title"], f"任务 {record['id']} · {STATUS[record['status']]}"]
    for step in record["steps"]:
        if step["id"] in proof:
            item = proof[step["id"]]
            output = item.get("output", {})
            lines += [f"{step['id']} · {TOOLS[step['tool']].title} · {item['status']}",
                      output.get("text", item.get("detail", ""))]
            if source := output.get("source"):
                lines.append(f"证据来源 {BOUND.get(source, source)}")
            if response := output.get("response"):
                metrics = output["metrics"]
                lines += [f"求解器 {response['solver']['method']} · 配置 SHA-256 {response['configuration_sha256']}",
                          f"横向 RMS {metrics['body_y_rms_m_s2']:.6g} m/s^2 · 垂向 RMS {metrics['body_z_rms_m_s2']:.6g} m/s^2",
                          f"最大减载率 {metrics['wheel_unloading']:.2%} · 二系垂向行程 {metrics['secondary_travel_peak_mm']:.5g} mm"]
            if "pareto" in output:
                args = step["args"]
                candidates = "、".join(output["pareto"]) or "无"
                lines += [f"减载率上限 {args['unloading_ratio']:.2%} · 二系行程上限 {args['travel_mm']:.5g} mm",
                          f"Pareto 候选 {candidates}"]
            lines.extend(str(ref) for ref in dict.fromkeys(output.get("references", [])))
    lines.append("测量分析、公开数值资料及教学计算的来源见工具明细。实车结论需独立验证。")
    for text in lines:
        text = text.replace("²", "^2").replace("³", "^3")
        while text:
            end = len(text)
            while page.stringWidth(text[:end],normal,10) > 520:
                end -= 1
            if y < 45:
                page.showPage()
                page.setFont(normal,10)
                y = 790
            page.drawString(36,y,text[:end])
            y -= 17
            text = text[end:]
    page.save()
    return stream.getvalue()


@st.fragment(run_every=.8)
def _live(identifier, total):
    from rail_vehicle.db import get_connection

    with get_connection() as db:
        record = db.execute("SELECT status,control,heartbeat FROM workbench_jobs WHERE id=?",(identifier,)).fetchone()
        done = db.execute("SELECT count(*) FROM workbench_results WHERE job_id=?",(identifier,)).fetchone()[0]
    if record["status"] not in ("queued","running"):
        st.rerun()
    if record["status"]=="running" and time()-record["heartbeat"]>LEASE:
        recover()
        st.rerun()
    st.progress(done/max(1,total),text=f"{STATUS[record['status']]} · 已保存 {done}/{total} 个步骤")
    a,b,c = st.columns(3)
    if a.button("暂停任务",disabled=bool(record["control"])):
        update(identifier,"pause")
        st.rerun()
    if b.button("取消任务",disabled=bool(record["control"])):
        update(identifier,"cancel")
        st.rerun()
    if c.button("恢复待执行队列",disabled=record["status"]!="queued"):
        recover()
        dispatch()


def _result(record, proof):
    from rail_vehicle.pages.copilot import _plot, _scene

    for step in record["steps"]:
        item = proof.get(step["id"])
        if not item:
            continue
        title = TOOLS[step["tool"]].title
        with st.expander(f"{title} · {step['id']} · {item['status']}"):
            if item["status"]!="done":
                st.warning(item["detail"])
                continue
            data = item["output"]
            st.write(data["text"])
            st.caption(f"{BOUND.get(data['source'],data['source'])} · {item['elapsed_ms']} ms · {item['proof'][:12]}")
            if data.get("rows"):
                rows = data["rows"]
                st.dataframe(rows if isinstance(rows,list) else [{"步骤":key,**v["metrics"],"可行":v["feasible"]}
                                                                 for key,v in rows.items()],hide_index=True)
            if data.get("figure"):
                _plot(data["figure"],"ix_plot_"+step["id"])
            if data.get("citations"):
                st.json(data["citations"],expanded=False)
            if data.get("metrics"):
                st.json(data["metrics"],expanded=False)
            if data.get("references"):
                st.caption(" · ".join(data["references"]))
            if "navigation" in data:
                from rail_vehicle.ui import ANALYSIS, BOGIE, REPORT, SIMULATION, TWIN, VALIDATION, navigate

                destinations = {"analysis": ANALYSIS, "bogie": BOGIE, "simulation": SIMULATION,
                                "twin": TWIN, "validation": VALIDATION, "report": REPORT}
                if destination := destinations.get(data["navigation"]):
                    st.button(f"确认打开 {destination}", key=f"x_open_{step['id']}",
                              on_click=navigate, args=(destination,))
            if data.get("scene") and st.toggle("打开结构视图",key="ix_scene_open_"+step["id"]):
                _scene(data["scene"],"ix_scene_"+step["id"],None,None)
            if data.get("response") and st.toggle("打开求解回放",key="ix_sim_"+step["id"]):
                from rail_vehicle.pages.wheelrail_lab import _viewer

                _viewer(data["response"],"ix_render_"+step["id"])
    if proof:
        a,b = st.columns(2)
        public_record = {k:record[k] for k in ("id","title","rounds","limits","status","spent","cloud_calls")}
        if context := record["context"].get("engineering"):
            public_record["engineering_context"] = context
            public_record["input_sha256"] = record["context"]["sha256"]
        a.download_button("导出研究证据 JSON",serialize({"task":public_record,"tools":proof}),
                          "workbench-evidence.json","application/json",on_click="ignore")
        b.download_button("导出工程任务 PDF",_pdf(record,proof),"workbench-report.pdf","application/pdf",on_click="ignore")


def _comparison(record, proof):
    responses = {key:value["output"]["response"] for key,value in proof.items()
                 if value["status"]=="done" and value["output"].get("response")}
    if len(responses)<2 or not st.toggle("双视窗比较求解",key="ix_compare_"+record["id"]):
        return
    a,b = st.columns(2)
    keys = list(responses)
    base = a.selectbox("基准计算",keys,key="ix_baseline_"+record["id"])
    alternative = b.selectbox("候选计算",[key for key in keys if key!=base],key="ix_alternative_"+record["id"])
    from rail_vehicle.pages.wheelrail_lab import _viewer

    try:
        _viewer(responses[base],"ix_compare_view",responses[alternative])
    except ValueError:
        st.info("双视窗使用同线路、同速度、同模型与同一时间网格。不同线路可在各步骤分别回放。")


def _draft(record, proof, config):
    state = st.session_state
    identifier = record["id"]
    pending = state.get("ix_proposed")
    if pending and pending[0]==identifier:
        _,origin,steps = pending
        st.subheader("待审批结构化计划")
        st.dataframe([{"步骤":s["id"],"工具":TOOLS[s["tool"]].title,"参数":serialize(s["args"]),
                       "依赖":"、".join(s["depends"]),"成本":TOOLS[s["tool"]].cost}
                      for s in steps],hide_index=True)
        st.caption(f"本轮 {len(steps)} 步 · 成本 {sum(TOOLS[s['tool']].cost for s in steps)} · 只读工程工具")
        a,b = st.columns(2)
        if a.button("批准并加入本地队列",type="primary"):
            record_round(identifier,steps,origin)
            state.pop("ix_proposed",None)
            dispatch()
            st.rerun()
        if b.button("撤回本轮草案"):
            state.pop("ix_proposed",None)
            st.rerun()
        return
    if (replay := archived_round(record)) is not None:
        st.caption("复算使用归档的批次、资料与参数。每轮可核对原始步骤后批准。")
        if st.button("查看归档复算计划", type="primary"):
            state["ix_proposed"] = (identifier, "replay", replay)
            st.rerun()
        return
    st.subheader("追加研究目标" if record["status"]=="review" else "规划本轮研究")
    question = st.text_area("本轮目标",value="" if record["status"]=="review" else record["title"],
                            max_chars=1200,key=f"ix_goal_{identifier}_{len(record['rounds'])}")
    mode = st.radio("规划模式",("本地工程分析","云端结构化规划"),horizontal=True)
    cloud = mode=="云端结构化规划"
    allow = False
    if cloud:
        config = config or AIConfig.from_sources()
        st.caption(f"每请求至多 {COMPLETION_TOKENS} 个输出 token，最多 3 次请求。发送本轮目标、工具规格与状态信号。")
        allow = st.checkbox("同意发送本轮目标与压缩状态至已配置服务",
                            key=f"ix_cloud_consent_{identifier}_{len(record['rounds'])}_{record['cloud_calls']}")
        if not config or not config.enabled or not config.api_key:
            st.info("云端服务尚未配置，可切换至本地工程分析。")
    a,b = st.columns(2)
    if a.button("生成本轮计划",type="primary",
                disabled=cloud and (not allow or not config or not config.enabled or not config.api_key)):
        if cloud:
            with st.spinner("正在接收结构化计划…"):
                note_cloud_call(identifier)
                client = planning_client(config)
                try:
                    steps = cloud_proposal(client,config.provider,config.model,question,record,proof)
                finally:
                    client.close()
            origin = "cloud"
        else:
            steps = validate(local_proposal(question, record["steps"], proof, record["context"].get("engineering")),
                             record["steps"], record["limits"])
            origin = "local"
        state["ix_proposed"] = (identifier,origin,steps)
        st.rerun()
    if record["status"]=="review" and b.button("结束研究并归档"):
        update(identifier,"finish")
        st.rerun()


def render(work, config):
    st.header("工程研究工作台")
    if work.data:
        st.caption(f"{work.data.source_description} · {work.data.vehicle['vehicle_code']} · 批次 {work.data.batch['id']} · "
                   f"{len(work.data.rows):,} 点"+(f" · 对照批次 {work.other.batch['id']}" if work.other else ""))
    else:
        st.caption("当前未绑定观测批次，可进行公开基准校准或教学求解。")
    sidebar, body = st.columns([1,3],gap="large")
    try:
        project = (work.engineering or {}).get("project")
        entries = listing(project=project)
        index = {row["id"]:row for row in entries}
        state=st.session_state
        with sidebar:
            st.subheader("本机任务")
            if work.data:
                st.caption("创建任务会把所选观测与对照批次快照保存到本机，用于复算和恢复。")
            with st.form("ix_new_task"):
                title = st.text_area("研究目标",max_chars=1200,height=98)
                budget = st.slider("累计工具成本",4,34,24)
                priority = st.checkbox("优先执行")
                submit = st.form_submit_button("新建研究任务",type="primary")
            if submit:
                from dataclasses import replace
                from rail_vehicle.knowledge_library import passages

                source = replace(work, notes=tuple(passages(project, title))) if project else work
                state["ix_select_task"] = create(title, source, cost=budget, priority=int(priority))
                st.rerun()
            if not index:
                body.info("创建研究任务后可预览计划、批准运行并查看实际证据。")
                return
            if replay := state.pop("x_select_replay", None):
                state["ix_select_task"] = replay
            identifier = st.selectbox("选择任务",list(index),key="ix_select_task",
                                      format_func=lambda key:f"{STATUS[index[key]['status']]} · {index[key]['title'][:44]}")
        with body:
            record = job(identifier)
            state["x_current_task"] = identifier
            st.subheader(record["title"][:140])
            context = record["context"].get("engineering")
            state["x_task_project"] = (context or {}).get("project")
            if context:
                with st.expander("绑定的工程上下文"):
                    st.json(context, expanded=False)
                    if "simulation" in context:
                        from rail_vehicle.nonlinear_vehicle import read_case
                        from rail_vehicle.ui import SIMULATION, navigate

                        st.button("在动力学中打开已绑定工况", on_click=navigate, args=(SIMULATION,),
                                  kwargs={"sim_case": "wheelrail", "wr_case": read_case(serialize(context["simulation"])),
                                          "wr_mode": "整车与线路"})
            if selected:=record["context"]["data"]:
                st.caption(f"任务快照 · {selected['batch']['file_name']} · {len(selected['rows']):,} 点 · "
                           f"数据摘要 {selected['identity'][:12]}")
            st.caption(f"{STATUS[record['status']]} · {len(record['rounds'])}/{record['limits']['rounds']} 轮 · "
                       f"累计执行 {record['spent']:.1f} s · 云端请求 {record['cloud_calls']}/{record['limits']['cloud_calls']}")
            if record["status"] in ("running","queued"):
                _live(identifier,len(record["steps"]))
                return
            proof = results(identifier)
            if record["error"]:
                st.warning(record["error"])
            if record["status"] in ("paused","error","cancelled"):
                if st.button("继续已批准的步骤"):
                    update(identifier,"resume")
                    dispatch()
                    st.rerun()
            _result(record,proof)
            if record["status"] in ("draft","review"):
                _draft(record,proof,config)
        _comparison(record,proof)
        if record["status"] in ("review", "complete") and proof and st.button("从归档新建复算"):
            state["x_select_replay"] = duplicate_for_replay(identifier)
            st.rerun()
    except (ValueError,AIProviderUnavailable,TimeoutError) as exc:
        st.warning(str(exc))
    except (sqlite3.Error,OSError,zlib.error):
        st.warning("任务存储或本机工作进程暂时无法访问。")
