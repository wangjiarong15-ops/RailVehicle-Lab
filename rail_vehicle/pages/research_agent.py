"""Approval, evidence, and replay for persisted engineering tasks."""

from dataclasses import replace
from io import BytesIO
import re
import sqlite3
import zlib

import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.engineering_trials import PARAMETERS, check_binding, lateral_evidence
from rail_vehicle.nonlinear_vehicle import WheelRailCase
from rail_vehicle.research_agent import AgentJob, Plan, approved, graph, list_tasks, reservation, restore
from rail_vehicle.ui import plot_chart
from rail_vehicle.virtual_trials import json_text

JOB = "viii_agent_job"
TITLE = "工程研究任务"
TOOL_NAMES = {"observation":"核对输入证据","engineering_knowledge":"检索资料并登记假设","dynamic_response":"31 自由度求解",
              "pareto":"Pareto 筛选","independent_response":"独立 RK45 复算",
              "speed_probe":"速度扰动复核","report":"生成证据报告"}


def accepts(prompt):
    return ("横向" in prompt and any(word in prompt for word in ("试验","仿真","候选"))
            and any(word in prompt.lower() for word in ("轮重","pareto","多目标","独立复算")))


def propose(work, case, prompt):
    base = case or WheelRailCase(duration_s=1.25)
    changes = {}
    expressions = {
        "wheel_unloading": r"减载(?:率)?\s*(?:上限(?:为|改为)?|不超过|≤|小于等于)\s*(\d+(?:\.\d+)?)\s*[%％]",
        "travel_mm": r"行程\s*(?:上限(?:为|改为)?|不超过|≤|小于等于)\s*(\d+(?:\.\d+)?)\s*mm",
        "budget": r"预算(?:为|改为|上限|不超过)?\s*(\d+)\s*次",
    }
    for key, pattern in expressions.items():
        if match:=re.search(pattern,prompt):
            changes[key] = int(match[1]) if key=="budget" else float(match[1])/(100 if key=="wheel_unloading" else 1)
    plan = Plan(base,observation=lateral_evidence(work),**changes)
    existing = st.session_state.get(JOB)
    if existing and existing.state()[0] in ("running","paused","cancelling"):
        raise ValueError("已有获批任务，请先完成或取消。")
    st.session_state[JOB] = AgentJob(plan)


def _edit(plan):
    keys = list(PARAMETERS)
    selected = st.multiselect("设计变量（最多三个）",keys,default=[a[0] for a in plan.axes],
                              format_func=lambda key:PARAMETERS[key][0],max_selections=3,key="viii_variable_"+plan.payload()["task_id"])
    with st.form("viii_plan_"+plan.payload()["task_id"],border=False):
        previous = {name:(low,high) for name,low,high in plan.axes}
        result = []
        for key in selected:
            label,unit,minval,maxval = PARAMETERS[key]
            lower,upper = previous.get(key,(minval+.25*(maxval-minval),minval+.75*(maxval-minval)))
            a,b = st.columns(2)
            lo = a.number_input(f"{label}下限（{unit}）",float(minval),float(maxval),float(lower),format="%.3f")
            hi = b.number_input(f"{label}上限（{unit}）",float(minval),float(maxval),float(upper),format="%.3f")
            result.append((key,lo,hi))
        a,b,c = st.columns(3)
        design = a.selectbox("试验设计",("factorial","lhs"),index=int(plan.design=="lhs"),
                             format_func={"factorial":"两水平全因子","lhs":"拉丁超立方"}.get)
        draws = b.number_input("拉丁超立方案例数",2,8,plan.draws)
        budget = c.number_input("最多求解次数",5,13,plan.budget)
        a,b,c = st.columns(3)
        unload = a.number_input("轮重减载率上限（%）",2.,95.,float(plan.wheel_unloading*100))
        travel = b.number_input("二系垂向行程上限（mm）",4.,100.,float(plan.travel_mm))
        robust = c.checkbox("复算速度扰动",value=plan.robustness)
        options = (("lateral","vertical"),("lateral",),("vertical",))
        objectives = st.selectbox("RMS 优化目标",options,index=options.index(plan.objectives),
                                  format_func=lambda x:" + ".join("横向" if y=="lateral" else "垂向" for y in x))
        save = st.form_submit_button("更新待审批计划")
    if save:
        try:
            updated = replace(plan,axes=tuple(result),design=design,draws=draws,budget=budget,
                              wheel_unloading=unload/100,travel_mm=travel,robustness=robust,objectives=objectives)
            st.session_state[JOB] = AgentJob(updated)
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))


def _start(job, data):
    try:
        check_binding(job.request,data)
        approved(job.request)
        job.start()
    except (ValueError,sqlite3.Error) as exc:
        st.session_state["viii_agent_error"] = str(exc) if isinstance(exc,ValueError) else "无法写入本地审批记录。"


@st.fragment(run_every=.65)
def _running(job):
    status,stage,_,_ = job.state()
    if status in ("complete","error","cancelled"):
        st.rerun()
    steps = graph(job.request)
    latest = steps[min(stage,len(steps)-1)]
    st.progress(stage/len(steps),text=f"{stage}/{len(steps)} 步 · {TOOL_NAMES[latest.tool]}")
    a,b = st.columns(2)
    if status=="paused":
        a.button("继续研究任务",on_click=job.start)
    else:
        a.button("暂停研究任务",on_click=job.pause,disabled=status!="running")
    b.button("取消本轮研究",on_click=job.cancel,disabled=status=="cancelling")


def _pdf(report):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    from rail_vehicle.analysis_report import _font_names

    stream = BytesIO()
    page = Canvas(stream,pagesize=A4)
    normal,bold = _font_names()
    page.setFont(bold,16)
    page.drawString(36,795,"RailVehicle-Lab 工程研究报告")
    plan = report["plan"]
    rank = report["ranking"]
    lines = [report["evidence_id"],f"模型 {report['model']} · 预算 {plan['budget']} 次求解",
             f"轮重减载率上限 {plan['wheel_unloading']:.1%} · 二系垂向行程上限 {plan['travel_mm']:.1f} mm"]
    if event:=report["evidence"]["event"]:
        lines += [f"数据 {event['source_label']} · {event['event_id']} · 样本 {event['index_1based']}",
                  f"数据 SHA-256 {event['dataset_sha256']}"]
    for key,row in rank["rows"].items():
        if metrics:=row.get("metrics"):
            lines.append(f"{key} · 横向 {metrics['body_y_rms_m_s2']:.5g} / 垂向 {metrics['body_z_rms_m_s2']:.5g} m/s^2"
                         f" · 减载 {metrics['wheel_unloading']:.1%} · 行程 {metrics['secondary_travel_peak_mm']:.2f} mm")
        else:
            lines.append(f"{key} · 求解无效")
    if compare:=report["difference"]:
        lines.append(f"RK45 复算 RMS 减少量 {compare['reduction']:.6g} m/s^2 · 求解差异 {compare['solver_difference']:.3g}")
    lines += ["完整试验设计、约束、实际工具记录与证据编号见同任务 JSON。",
              "试算用于比较假设。机械因果、实车优化效果与认证需要独立验证。"]
    page.setFont(normal,9)
    for index,line in enumerate(lines):
        page.drawString(36,766-index*19,line)
    page.save()
    return stream.getvalue()


def results(done):
    report = done["report"]
    ranked = report["ranking"]
    st.subheader("Pareto 与独立复算")
    rows = [{"工况":key,"参数":str(row["parameters"]),"横向 RMS（m/s²）":row.get("metrics",{}).get("body_y_rms_m_s2"),
             "垂向 RMS（m/s²）":row.get("metrics",{}).get("body_z_rms_m_s2"),
             "减载率":row.get("metrics",{}).get("wheel_unloading"),"行程（mm）":row.get("metrics",{}).get("secondary_travel_peak_mm"),
             "约束":"、".join(row["violations"]) or "通过","非支配":key in ranked["pareto"]}
            for key,row in ranked["rows"].items()]
    st.dataframe(rows,hide_index=True)
    if ranked["interaction_effect"] is not None:
        st.caption(f"第一目标的两因素交互效应 {ranked['interaction_effect']:.5g} m/s²。")
    fig = go.Figure()
    for selected,label in ((True,"可行非支配"),(False,"其余结果")):
        subset = [r for r in rows if r["非支配"]==selected and r["横向 RMS（m/s²）"] is not None]
        if subset:
            fig.add_scatter(x=[r["横向 RMS（m/s²）"] for r in subset],y=[r["垂向 RMS（m/s²）"] for r in subset],
                            text=[r["工况"] for r in subset],mode="markers",name=label)
    fig.update_layout(height=285,xaxis_title="车体横向 RMS（m/s²）",yaxis_title="车体垂向 RMS（m/s²）")
    plot_chart(fig,key="viii_pareto")
    if comparison:=report["difference"]:
        st.write(f"独立 RK45 复算减少量 {comparison['reduction']:.6g} m/s²，求解器差异 {comparison['solver_difference']:.4g} m/s²。")
        if not comparison["supported"]:
            st.info("独立复算未支持当前约束下的主要目标改善。")
    else:
        st.info("没有符合当前约束、可进入独立复算的候选。")
    if stress:=report["speed_probe"]:
        st.caption(f"速度扰动试算的减少量 {stress['reduction']:.6g} m/s²，"
                   f"成对约束{'通过' if stress['within_declared_constraints'] else '未通过'}。")
    with st.expander("实际工具记录"):
        st.dataframe(report["tools"],hide_index=True)
        for source in report["hypotheses"]["references"]:
            st.write(source["title"],source["source"])
    choice=ranked["choice"]
    if choice and "response" in done["verify_choice"] and "response" in done["verify_base"]:
        if st.toggle("同步回放原始方案与候选方案"):
            from rail_vehicle.pages.wheelrail_lab import _viewer
            base,alt = done["verify_base"]["response"],done["verify_choice"]["response"]
            try:
                _viewer(base,"viii_verify_3d",alt)
            except ValueError:
                st.info("双视窗要求同线路、同速度和同质量配置。可在各自条件中查看。")
                scenario=st.radio("观察",("原始方案","候选方案"),horizontal=True)
                _viewer(base if scenario=="原始方案" else alt,"viii_verify_single")
    a,b=st.columns(2)
    a.download_button("下载工程研究证据 JSON",json_text(report),"engineering-agent.json","application/json",on_click="ignore")
    b.download_button("下载工程研究报告 PDF",_pdf(report),"engineering-agent.pdf","application/pdf",on_click="ignore")


def render(data=None, case=None):
    state = st.session_state
    if msg:=state.pop("viii_agent_error",None):
        st.warning(msg)
    job = state.get(JOB)
    if not job:
        if case and st.button("创建多目标参数试验"):
            state[JOB] = AgentJob(Plan(case))
            st.rerun()
        records = list_tasks()
        if records:
            by_id = {row["task_id"]:row["created_at"] for row in records}
            task = st.selectbox("本机研究归档",list(by_id),format_func=lambda t:f"{by_id[t]} UTC · {t[9:21]}")
            if st.button("打开研究归档"):
                try:
                    plan,done = restore(task)
                except (ValueError,zlib.error,sqlite3.Error) as error:
                    st.warning(str(error))
                    return
                restored = AgentJob(plan)
                if "report" in done:
                    restored.status,restored.results = "complete",done
                state[JOB] = restored
                st.rerun()
        return
    status,_,error,_ = job.state()
    if status in ("running","paused","cancelling"):
        _running(job)
        return
    if status=="complete":
        results(job.results)
    else:
        plan=job.request
        st.subheader("待审批工程任务")
        if event:=plan.observation:
            st.write(f"{event['source_label']} · {event['event_id']} · 样本 {event['index_1based']}")
        st.caption(f"本地白名单 · 最多 {reservation(plan)} 次积分 · 减载率 ≤ {plan.wheel_unloading:.0%} · 二系行程 ≤ {plan.travel_mm:g} mm")
        if error:
            st.warning(error)
        if status=="cancelled":
            st.info("已暂停本轮执行，完成的工具步骤已保留。")
            if st.button("准备恢复完成的步骤"):
                state[JOB]=AgentJob(plan)
                st.rerun()
        else:
            with st.expander("调整目标、变量及预算"):
                _edit(plan)
            st.dataframe([{"步骤":s.id,"工具":TOOL_NAMES[s.tool],"依赖":"、".join(s.depends)}
                          for s in graph(plan)],hide_index=True)
            st.button("批准并执行工程研究",type="primary",on_click=_start,args=(job,data))
    st.button("返回工程研究归档",on_click=state.pop,args=(JOB,None))
