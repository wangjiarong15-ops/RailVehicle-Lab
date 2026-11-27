"""Approval, local checkpoints, and synchronized replay of engineering trials."""

from dataclasses import replace
import re
import sqlite3

import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.engineering_trials import (HYPOTHESES, PARAMETERS, PHASES, Trial, TrialJob, check_binding,
                                             history, lateral_evidence, load_trial, save_request, trial_report_pdf)
from rail_vehicle.ui import plot_chart
from rail_vehicle.virtual_trials import json_text

JOB="vii_trial_job"
ARCHIVE="vii_trial_archive"


def draft_from_prompt(work,base,prompt):
    limit=re.search(r"(?:二系)?(?:垂向)?行程\s*(?:不超过|上限(?:为)?|≤)\s*([+-]?\d+(?:\.\d+)?)\s*mm",prompt)
    return Trial(base,HYPOTHESES,float(limit[1]) if limit else 35.,lateral_evidence(work))


def revise_draft(job,data,prompt):
    choice=re.fullmatch(r"\s*(?:请)?(?:把|将)?(?:二系)?(?:垂向)?行程上限(?:改为|设为|调整为)\s*"
                        r"([+-]?\d+(?:\.\d+)?)\s*mm[。!！]?\s*",prompt)
    if choice is None:
        return False
    if job is None or job.state()[0]!="draft":
        raise ValueError("请先建立尚未批准的参数试验草案。")
    check_binding(job.request,data)
    set_draft(replace(job.request,travel_mm=float(choice[1])))
    return True


def set_draft(request):
    s=st.session_state
    job=s.get(JOB)
    if job and job.state()[0] in ("running","paused","cancelling"):
        raise ValueError("已有获批试验正在运行，请先返回查看。")
    s[JOB]=TrialJob(request)
    s.pop(ARCHIVE,None)


def _approve(job,data):
    try:
        check_binding(job.request,data)
        save_request(job.request)
    except (ValueError,sqlite3.Error) as exc:
        st.session_state["vii_trial_error"]=str(exc) if isinstance(exc,ValueError) else "无法保存本地审批，请核对数据目录。"
        return
    job.start()


@st.fragment(run_every=.6)
def _progress(job):
    status,stage,_,_=job.state()
    if status in ("complete","error","cancelled"):
        st.rerun()
    done=job.started_solves
    budget=job.request.payload()["budget"]
    st.progress(min(.98,done/budget),text=f"{PHASES[min(stage,len(PHASES)-1)]} · 已启动 {done}/{budget} 次积分")
    a,b=st.columns(2)
    if status=="paused":
        a.button("继续已批准的参数试验",on_click=job.start)
    else:
        a.button("暂停参数试验",on_click=job.pause,disabled=status!="running")
    b.button("取消参数试验",on_click=job.cancel,disabled=status=="cancelling")


def _observation(evidence):
    if not evidence:
        st.caption("独立实验计划 · 教学工况")
        return
    st.markdown("**原始观测证据**")
    st.write(evidence["vehicle_code"]+" · "+evidence["batch"]+" · "+evidence["source_label"])
    st.caption(f"{evidence['event_id']} · {evidence['event_start']} 至 {evidence['event_end']} · "
               f"当前窗口样本 {evidence['index_1based'][0]}–{evidence['index_1based'][1]}")
    st.write(f"规则阈值 {evidence['threshold_m_s2']:g} m/s² · 峰值 {evidence['peak_m_s2']:g} m/s² · "
             f"区段速度中位数 {evidence['median_event_speed_kmh']:g} km/h")
    with st.expander("采样质量与证据摘要"):
        st.write(evidence["sample_quality"],evidence["window_metrics"])
        st.code(evidence["dataset_sha256"])
        st.write({"CSV 原始行":evidence["csv_lines"],"重复时刻指纹":evidence["anchor_sha256"]})


def _draft(job,data):
    request=job.request
    _observation(request.observation)
    st.subheader("工程任务待审批")
    st.caption("本地试验依次读取观测、计算共同基准与参数对照、用独立 RK45 复算、核对约束并归档。")
    st.write(f"基准速度 {request.base.speed_m_s:g} m/s · {request.base.duration_s:g} s · "
             f"车体质量 {request.base.vehicle.body_mass_kg:g} kg · 二系垂向行程上限 {request.travel_mm:g} mm · "
             f"最多 {request.payload()['budget']} 次积分")
    st.dataframe([{"对照":PARAMETERS[key][0],"值":value,"单位":PARAMETERS[key][1]}
                  for key,value in request.changes],hide_index=True)
    if request.observation:
        st.caption("可继续输入「将二系垂向行程上限改为 40 mm」，核对后再审批。")
    a,b=st.columns(2)
    a.button("批准并运行本地参数试验",type="primary",on_click=_approve,args=(job,data))
    b.button("撤回草案",on_click=st.session_state.pop,args=(JOB,None))


def _label(row):
    return row["name"]+(f" {row['value']:g} {PARAMETERS[row['parameter']][1]}" if row["parameter"] else "")


def _summary(report,job=None):
    st.subheader("参数对照结果")
    st.caption(f"31 自由度教学工况 · {report['solves']} 次求解 · {report['evidence_id']}")
    _observation(report["request"]["observation"])
    rows=[report["baseline"],*report["cases"]]
    st.dataframe([{"工况":_label(row),"横向 RMS（m/s²）":row.get("metrics",{}).get("body_y_rms_m_s2"),
                   "二系行程（mm）":row.get("metrics",{}).get("secondary_travel_peak_mm"),
                   "最低轮重（kN）":row.get("metrics",{}).get("wheel_normal_min_kn"),
                   "末轴终点（m）":row.get("trailing_station_m"),
                   "约束":row.get("error") or "、".join({"contact_domain":"接触域","secondary_travel":"行程超限"}[code]
                                                    for code in row["violations"]) or "满足"}
                  for row in rows],hide_index=True)
    if report["sensitivity"] is not None:
        st.caption(f"首末输入点的横向 RMS 割线灵敏度为 {report['sensitivity']:.6g} m/s²，"
                   f"对应每 1 {PARAMETERS[rows[-1]['parameter']][1]}。")
    if any(row["parameter"]=="speed" for row in rows):
        st.caption("速度工况共用时长，末轴覆盖里程各异。方案结论须回到同一线路区间复核。")
    comparison=report["comparison"]
    if comparison and comparison["eligible"]:
        st.write(f"独立 RK45 对照 · {_label(comparison['candidate'])} · RMS 减少量 "
                 f"{comparison['reduction_m_s2']:.6g} m/s² · 数值变化量 {comparison['numerical_variation_m_s2']:.4g} m/s²")
        if comparison["improves_this_model"]:
            st.info("候选在本次模型和约束下出现可辨认的数值改善。")
        else:
            st.info("当前对照不足以支持数值改善。")
    else:
        st.warning("本轮缺少满足约束且独立复算通过的候选。")
    with st.expander("候选解释及核查资料"):
        st.write("先核对通道方向、重复时刻、轮轨激励和可比速度，再用同车横向位移或悬挂台架试验区分刚度与阻尼。")
        st.write("所列结构为参数作用位置。异常与任一机械故障之间仍需独立因果证据。")
        for source in report["sources"]:
            st.write(source["title"],source["source"])
    if job:
        candidates=[row for row in rows if row.get("response_id") in job.responses]
        if comparison and comparison["eligible"]:
            candidates.extend([comparison["baseline"],comparison["candidate"]])
        if candidates:
            _visualize(candidates,job)
    a,b=st.columns(2)
    a.download_button("下载参数试验证据 JSON",json_text(report),"engineering-trial.json",on_click="ignore")
    b.download_button("下载参数试验报告 PDF",trial_report_pdf(report),"engineering-trial.pdf","application/pdf",on_click="ignore")


def _visualize(cases,job):
    if not st.toggle("打开参数对照曲线和三维",key="vii_open_scene"):
        return
    from rail_vehicle.pages.wheelrail_lab import _viewer

    selected=st.selectbox("选择计算工况",range(len(cases)),
                          format_func=lambda i:_label(cases[i])+" · "+cases[i]["solver"]["method"])
    row=cases[selected]
    result=job.responses[row["response_id"]]
    figure=go.Figure()
    for item in cases:
        if item["solver"]["method"]!="DOP853":
            continue
        response=job.responses[item["response_id"]]
        figure.add_scatter(x=response["time_s"],y=[point[0] for point in response["ddq"]],name=_label(item))
    figure.add_vline(x=row["peak_time_s"],line_dash="dot")
    figure.update_layout(height=305,xaxis_title="仿真时间（s）",yaxis_title="车体相对横向加速度（m/s²）")
    plot_chart(figure,key="vii_curves")
    st.caption("三维由当前仿真帧驱动。轮轨视窗可跳转所选通道的计算峰值或切换观察部位。")
    _viewer(result,"vii_scene_"+row["response_id"],initial_focus={
        "primary_k":"front_primary","secondary_k":"front_secondary","secondary_c":"front_secondary"
    }.get(row["parameter"],"vehicle"))


def render_panel(data=None):
    s=st.session_state
    if message:=s.pop("vii_trial_error",None):
        st.warning(message)
    if job:=s.get(JOB):
        status,_,error,report=job.state()
        if status=="draft":
            _draft(job,data)
            return
        if status in ("running","paused","cancelling"):
            _progress(job)
            return
        if report:
            _summary(report,job)
        elif error:
            st.warning(error)
            st.button("重试已保存的参数试验",on_click=_approve,args=(job,data))
        elif status=="cancelled":
            st.info("本轮已取消。已批准的参数可从本机历史恢复。")
        st.button("返回参数试验列表",on_click=s.pop,args=(JOB,None))
        return
    try:
        records=history()
    except sqlite3.Error:
        st.warning("本地试验历史暂不可用。")
        return
    if not records:
        st.caption("暂无已批准的本地参数试验。")
        return
    by_id={item["trial_id"]:item for item in records}
    trial_id=st.selectbox("本地试验记录",list(by_id),format_func=lambda key:
                          f"{by_id[key]['created_at']} UTC · {'已归档' if by_id[key]['finished'] else '已批准'} · {key[6:18]}")
    if st.button("打开试验历史",key="vii_history_load"):
        try:
            s[ARCHIVE]=load_trial(trial_id)
        except (ValueError,KeyError,sqlite3.Error) as exc:
            st.warning(str(exc) if isinstance(exc,ValueError) else "无法读取该本地记录。")
    if archive:=s.get(ARCHIVE):
        request,report=archive
        if request.payload()["trial_id"]!=trial_id:
            return
        if report:
            _summary(report)
        else:
            _observation(request.observation)
            st.info("该记录保留了批准的试验配置，响应尚未归档。")
        if st.button("恢复配置并重新审批"):
            set_draft(request)
            st.rerun()


def render_matrix(base):
    st.subheader("31 自由度参数试验")
    st.caption("同一基准独立改变一项参数，可比较一至三个值。局部灵敏度按端点割线给出。")
    presets={"speed":"18, 22", "load":"0.9, 1.1", "primary_k":"0.85, 1.15",
             "secondary_k":"0.85, 1.15","secondary_c":"0.75, 1.25","radius":"550, 650",
             "cant":"60, 90","irregularity":"0.8, 1.2"}
    if not st.session_state.get(JOB):
        parameter=st.selectbox("扫描参数",list(PARAMETERS),format_func=lambda key:PARAMETERS[key][0])
        with st.form(f"vii_matrix_{parameter}"):
            text=st.text_input(f"对照值（{PARAMETERS[parameter][1]}）",presets[parameter])
            travel=st.number_input("二系垂向行程上限（mm）",4.,100.,35.)
            generate=st.form_submit_button("生成参数试验计划",type="primary")
        if generate:
            try:
                values=tuple(float(piece) for piece in re.split("[,，、]",text))
                set_draft(Trial(base,tuple((parameter,value) for value in values),travel))
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))
    render_panel()
