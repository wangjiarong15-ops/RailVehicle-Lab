"""Wheelset benchmark, nonlinear vehicle, and approved curve studies."""

from dataclasses import replace
import re

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.dynamics_registry import solve
from rail_vehicle.nonlinear_vehicle import (MODEL, WheelRailCase, case_config, frequency_response, performance,
                                             read_case, tangent_stability)
from rail_vehicle.replay import ReplayState
from rail_vehicle.ui import COPILOT,SIMULATION,navigate,plot_chart
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import isolated_wheelset
from rail_vehicle.wheel_track import template
from rail_vehicle.wheelrail_playback import playback
from rail_vehicle.wheelrail_study import PHASES,StudyPlan,WheelRailStudy,restore_study,study_pdf

ROUTES={"curve":"缓和曲线 → 圆曲线","straight":"直线","reverse":"反向曲线"}
METRICS={"body_y_rms_m_s2":"车体相对横向 RMS（m/s²）","body_z_rms_m_s2":"车体相对垂向 RMS（m/s²）",
         "roll_peak_mrad":"车体相对侧滚峰值（mrad）","secondary_travel_peak_mm":"二系垂向行程（mm）",
         "primary_travel_peak_mm":"一系垂向行程（mm）","wheel_normal_min_kn":"最低法向轮重（kN）",
         "wheel_unloading":"相对静载减载率","tread_y_over_q":"踏面 |Y|/Q",
         "tread_lateral_peak_kn":"踏面横向力峰值（kN）","flange_proxy_peak_kn":"轮缘约束力峰值（kN）"}


@st.cache_data(max_entries=12,show_spinner=False)
def _run(case,accuracy="standard"):
    try:
        return solve(case,accuracy)
    except ImportError:
        raise ValueError("高级轮轨计算需要 signal 可选依赖（SciPy）。") from None


def wheelrail_intent(text):
    return (any(k in text for k in ("曲线通过","曲线半径","轮轨","悬挂不对称","蛇行稳定"))
            and any(k in text for k in ("试验","仿真","计算","比较","优化","分析","复算")))


def _numbers(text):
    return tuple(float(value.strip()) for value in re.split("[,，、]",text))


def prompt_plan(text,default=StudyPlan()):
    values={}
    number=r"[-+]?\d+(?:\.\d+)?"
    expression=rf"{number}(?:\s*[,，、]\s*{number})*"
    for key,label,units in (("speeds","速度",r"m/s(?![²^])"),("radii","(?:曲线)?半径",r"m(?![a-zA-Z/])")):
        if match:=re.search(rf"{label}\s*(?:为|[:：])?\s*({expression})\s*{units}",text):
            values[key]=_numbers(match[1])
    if match:=re.search(rf"(?:二系)?(?:垂向)?行程\s*(?:不超过|小于等于|上限(?:为)?|≤)\s*({number})\s*mm",text):
        values["max_travel_mm"]=float(match[1])
    if match:=re.search(r"预算\s*(?:为|[:：])?\s*([-+]?\d+)\s*次",text):
        values["maximum_solves"]=int(match[1])
    return replace(default,**values)


def _viewer(result,key,alternative=None):
    if not webgl_available():
        st.info("三维资源不可用。数值曲线与报告仍可下载。")
        return
    render_webgl(mode="正常运行",phase=0,state=ReplayState(),visible=PARTS,highlight=(),
                 view="整体视图",focus=None,labels=False,rails=True,exploded=False,spread=0,
                 camera_revision=0,continuous=False,height=610,key=key,simulation=playback(result,alternative))


def _parameters(case):
    s=st.session_state
    with st.form(f"wheelrail_case_{s.get('wr_rev',0)}",border=False):
        kind=st.selectbox("线路模板",("current",*ROUTES),format_func={"current":"保留当前线路",**ROUTES}.get)
        speed=st.number_input("运行速度（m/s）",5.,55.,float(case.speed_m_s))
        base_curve=next((abs(s.end_curvature_m_inv) for s in case.route.sections if s.end_curvature_m_inv),0.)
        radius=st.number_input("圆曲线半径（m）",125.,2000.,float(np.clip(1/base_curve,125,2000)) if base_curve else 450.)
        present_cant=next((s.end_cant_m for s in case.route.sections if s.end_cant_m),.08)
        cant=st.number_input("超高（mm，左转外轨升高为正）",-160.,160.,present_cant*1000)
        duration=st.number_input("输出时长（s）",1.,8.,float(case.duration_s),step=.5)
        professional=st.session_state["wr_options"]=="专业参数"
        if professional:
            p=case.contact
            contact_law=st.selectbox("踏面切向力模型",("saturated","linear"),index=int(p.law=="linear"),
                                     format_func={"saturated":"摩擦饱和","linear":"小蠕滑线性"}.get)
            gamma=st.number_input("等效锥度",0.,.35,p.conicity,step=.01)
            friction=st.number_input("摩擦系数",.05,.55,p.friction,step=.01)
            left=st.number_input("左轨垂向不平顺（mm）",0.,12.,case.route.left_m*1000)
            right=st.number_input("右轨垂向不平顺（mm）",0.,12.,case.route.right_m*1000)
            align=st.number_input("横向不平顺（mm）",0.,12.,case.route.align_m*1000)
            entries={}
            for key,label in (("primary_left","左一系"),("primary_right","右一系"),("secondary_left","左二系"),("secondary_right","右二系")):
                mount=getattr(case,key)
                with st.expander(label):
                    k=st.number_input(f"{label}刚度（kN/m）",100.,3000.,mount.stiffness_n_m/1000)
                    c=st.number_input(f"{label}阻尼（kN·s/m）",.5,120.,mount.damping_ns_m/1000)
                    hardening=st.number_input(f"{label}增刚段附加刚度（kN/m）",0.,4000.,mount.progression_n_m/1000)
                    quadratic=st.number_input(f"{label}二次阻尼（kN·s²/m²）",0.,300.,mount.quadratic_damping_ns2_m2/1000)
                    stop=st.number_input(f"{label}限位（mm）",max(40.,mount.progression_start_m*1000+1),160.,mount.stop_m*1000)
                    entries[key]=(k,c,hardening,quadratic,stop)
            anti=st.number_input("等效抗侧滚刚度（kN·m/rad）",0.,1000.,case.antiroll_n_m_rad/1000.)
        else:
            factor=st.selectbox("右二系设置",("保留当前","对称","轻度退化 −10%","退化 −20%"))
        precision=st.selectbox("积分精度",("standard","refined"),
                               format_func={"standard":"DOP853 常规","refined":"DOP853 精细"}.get)
        pending=st.form_submit_button("计算轮轨响应",type="primary",width="stretch")
    if pending:
        try:
            if professional:
                values={key:replace(getattr(case,key),stiffness_n_m=k*1000,damping_ns_m=c*1000,
                                    progression_n_m=hardening*1000,quadratic_damping_ns2_m2=quadratic*1000,
                                    stop_m=stop/1000) for key,(k,c,hardening,quadratic,stop) in entries.items()}
                route=replace(case.route,left_m=left/1000,right_m=right/1000,align_m=align/1000) if kind=="current" else (
                    template(kind,radius,cant/1000,left/1000,right/1000,align/1000))
                change={"contact":replace(case.contact,conicity=gamma,friction=friction,law=contact_law),
                        "route":route,
                        "antiroll_n_m_rad":anti*1000,**values}
            else:
                change={"route":case.route if kind=="current" else template(kind,radius,cant/1000)}
                if factor!="保留当前":
                    value={"对称":1.,"轻度退化 −10%":.9,"退化 −20%":.8}[factor]
                    change["secondary_right"]=replace(case.secondary_left,stiffness_n_m=case.secondary_left.stiffness_n_m*value,
                                                       damping_ns_m=case.secondary_left.damping_ns_m*value)
            s["wr_case"]=replace(case,speed_m_s=speed,duration_s=duration,**change)
            s["wr_accuracy"]=precision
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    with st.expander("工况 JSON 与自定义线路"):
        st.download_button("下载当前轮轨工况",json_text(case_config(case)),"wheelrail-case.json",
                           "application/json",on_click="ignore")
        uploaded=st.file_uploader("轮轨工况 JSON",type=["json"],key="wr_upload",max_upload_size=1)
        if uploaded is not None and st.button("载入轮轨工况"):
            try:
                s["wr_case"]=read_case(uploaded.getvalue())
            except ValueError as exc:
                st.error(str(exc))
            else:
                s["wr_rev"]=s.get("wr_rev",0)+1
                st.rerun()


def _performance(result):
    scores=performance(result)
    st.dataframe([{"指标":label,"本次计算":scores[key]} for key,label in METRICS.items()],hide_index=True)
    if scores["separated_samples"] or scores["linear_limit_samples"]:
        st.warning(f"输出采样中失去接触 {scores['separated_samples']} 帧，小蠕滑适用范围外 {scores['linear_limit_samples']} 帧。")
    st.caption("指标为实验模型中的相对响应及踏面力。Y/Q 仅统计有有效法向力的踏面；轮缘项单列。")
    st.download_button("下载响应及力证据 JSON",json_text(result),"wheelrail-response.json",
                       "application/json",on_click="ignore")


def _single(case):
    try:
        result=isolated_wheelset(case.contact,case.speed_m_s)
        modes=tangent_stability(case,[5.,10.,15.,20.,25.,30.,40.,50.,55.])
        frf=frequency_response(case,np.linspace(.1,15.,60).tolist())
    except (ValueError,ImportError) as exc:
        st.error(str(exc))
        return
    st.subheader("独立轮对与直线切线")
    st.caption(f"几何等效锥度给出的 Klingel 波长为 {result['kinematic_wavelength_m'] or '无有限值'} m。"
               "自由轮对计算曲线包含惯性和指定速度蠕滑。")
    st.line_chart({"秒":result["time_s"],"独立轮对横移 mm":[v[0]*1000 for v in result["state"]]},x="秒")
    st.dataframe([{"速度 m/s":r["speed_m_s"],"最大实部 1/s":r["growth_per_s"],
                   "领先极点频率 Hz":r["leading_hz"]} for r in modes["rows"]],hide_index=True)
    if modes["onset_brackets_m_s"]:
        brackets="、".join(f"{item['speed_m_s']:.2f} m/s" for item in modes["onset_estimates"])
        st.caption(f"直线小扰动增长率变号估计 {brackets}。仅对应当前参数和速度区间。")
    else:
        st.caption("当前速度网格未括住切线稳定性变号。")
    with st.expander("各速度下的振动模态"):
        speed_index=st.selectbox("模态速度（m/s）",range(len(modes["rows"])),
                                 format_func=lambda i:f"{modes['rows'][i]['speed_m_s']:g}")
        st.dataframe([{"阻尼频率 Hz":mode["damped_hz"],"固有频率 Hz":mode["natural_hz"],
                       "阻尼比":mode["damping_ratio"]} for mode in modes["rows"][speed_index]["modes"]],hide_index=True)
    st.line_chart(frf,x="hz",y="vertical_gain",x_label="频率 Hz",y_label="车体/共同轨面垂向位移")


@st.fragment(run_every=.7)
def _progress(job):
    state,step,message,report=job.state()
    if state in ("error","cancelled","complete"):
        st.rerun()
    st.progress(step/len(PHASES),text=f"{'暂停' if state=='paused' else '执行'} · {PHASES[min(step,len(PHASES)-1)]}")
    a,b=st.columns(2)
    if state=="paused":
        a.button("继续轮轨试验",on_click=job.start)
    else:
        a.button("暂停轮轨试验",on_click=job.pause,disabled=state!="running")
    b.button("取消轮轨试验",on_click=job.cancel,disabled=state=="cancelling")


def _comparison(report):
    c=report["comparison"]
    st.subheader("曲线试验证据")
    st.caption(f"{report['solves']} 次求解 · {report['evidence_id']}")
    cases=[("对称基准",report["symmetric"]),("退化基准",report["degraded"]),
           *((f"恢复 {trial['repair']:.0%}",trial) for trial in report["trials"])]
    st.dataframe([{"工况":name,"最坏二系行程 mm":group["max_travel_mm"],
                   "最大横向 RMS m/s²":group["worst_body_y_rms_m_s2"],
                   "原因":"、".join(group["violations"]) or "通过"} for name,group in cases],hide_index=True)
    if c and c["qualified"]:
        if c["candidate"]["worst_body_y_rms_m_s2"]<c["reference"]["worst_body_y_rms_m_s2"]:
            st.success("候选通过独立 RK45 复算，且本次网格的最坏横向 RMS 低于退化基准。")
        else:
            st.info("候选通过试验约束，最坏横向 RMS 未低于退化基准。")
        by_case=c["candidate"]["rows"]
        choice=st.selectbox("选择同线路的复算对照",range(len(by_case)),
                            format_func=lambda i:f"{by_case[i]['speed_m_s']:g} m/s · R {by_case[i]['radius_m']:g} m")
        a,b=c["reference"]["rows"][choice],by_case[choice]
        if st.toggle("打开复算双视窗",key="wr_show_pair"):
            with st.spinner("正在重建两条独立复算响应…"):
                original=_run(read_case(json_text(a["case"])),"independent")
                after=_run(read_case(json_text(b["case"])),"independent")
            if (original["evidence_id"],after["evidence_id"])!=(a["evidence_id"],b["evidence_id"]):
                st.error("三维计算证据与档案不一致。")
            else:
                _viewer(original,"wr_pair_view",after)
    else:
        st.warning("本轮无通过独立复算的可行候选。")
    st.download_button("下载轮轨试验证据 JSON",json_text(report),"wheelrail-study.json","application/json",on_click="ignore")
    st.download_button("下载轮轨试验 PDF",study_pdf(report),"wheelrail-study.pdf","application/pdf",on_click="ignore")


def _study(case):
    s=st.session_state
    st.subheader("对称、单侧退化与改进复算")
    st.caption("按已声明的速度和半径做离散试验。曲线使用同一缓和段、超高和不平顺。")
    prompt=s.get("wr_prompt")
    if prompt:
        st.text(prompt)
    job=s.get("wr_job")
    if job:
        status,stage,msg,report=job.state()
        if status in ("running","paused","cancelling"):
            _progress(job)
            return
        if status=="draft":
            _,p=job.request
            st.info(f"待审批 · {len(p.speeds)*len(p.radii)} 个速度与半径组合 · 至多 {p.maximum_solves} 次求解")
            st.write(f"速度 {', '.join(map(str,p.speeds))} m/s · 半径 {', '.join(map(str,p.radii))} m · "
                     f"二系行程上限 {p.max_travel_mm:g} mm · 右二系基准系数 {p.right_factor:g}")
            st.caption("DOP853 先求解对称、退化及候选。用 RK45 复算退化基准与最优可行候选。")
            st.button("批准执行轮轨试验",type="primary",on_click=job.start)
            st.button("返回编辑试验",on_click=s.pop,args=("wr_job",None))
            return
        if status=="error":
            st.error(msg)
            st.button("重新执行已批准试验",on_click=job.start)
        if status=="cancelled":
            st.info("本轮已取消。")
        if report:
            if expected:=s.get("wr_expected"):
                st.write("档案复算摘要一致。" if expected==report["evidence_id"] else "当前数值环境产生了新摘要。")
            _comparison(report)
        st.button("新建轮轨试验",on_click=s.pop,args=("wr_job",None))
        return
    uploaded=st.file_uploader("载入已归档的轮轨试验",type=["json"],key="wr_import_study",max_upload_size=1)
    if uploaded is not None and st.button("核验档案并准备复算"):
        try:
            base,plan,expected=restore_study(uploaded.getvalue())
            s["wr_expected"]=expected
            s["wr_job"]=WheelRailStudy((base,plan))
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))
    try:
        defaults=prompt_plan(prompt) if prompt else StudyPlan()
    except ValueError as exc:
        st.warning(str(exc))
        defaults=StudyPlan()
    with st.form(f"wr_study_params_{s.get('wr_prompt_rev',0)}"):
        a,b=st.columns(2)
        speeds=a.text_input("速度组合（m/s）",", ".join(map(str,defaults.speeds)))
        radii=b.text_input("曲线半径组合（m）",", ".join(map(str,defaults.radii)))
        a,b=st.columns(2)
        fraction=a.number_input("右二系退化系数",.65,.99,defaults.right_factor,step=.05)
        travel=b.number_input("二系垂向行程上限（mm）",4.,100.,defaults.max_travel_mm)
        a,b=st.columns(2)
        restores=a.text_input("右二系恢复比例",", ".join(map(str,defaults.repairs)))
        budget=b.number_input("模型求解次数预算",5,24,defaults.maximum_solves)
        create=st.form_submit_button("生成轮轨试验审批计划")
    if create:
        try:
            approved=StudyPlan(_numbers(speeds),_numbers(radii),_numbers(restores),fraction,travel,budget)
            s.pop("wr_expected",None)
            s["wr_job"]=WheelRailStudy((case,approved))
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))


def render_wheelrail(main):
    s=st.session_state
    case=s.get("wr_case") or WheelRailCase()
    s.setdefault("wr_mode","整车与线路")
    s.setdefault("wr_options","入门模板")
    mode=st.radio("轮轨工作区",("整车与线路","多工况审批","单轮对与稳定性"),key="wr_mode")
    if mode=="整车与线路":
        st.radio("参数模式",("入门模板","专业参数"),key="wr_options")
        _parameters(case)
    with main:
        st.subheader("高级轮轨动力学")
        st.caption("31 自由度实验模型 · 教学构型与声明的接触假设 · 独立实车验收待开展")
        if mode=="多工况审批":
            _study(case)
        elif mode=="单轮对与稳定性":
            _single(case)
        else:
            try:
                with st.spinner("正在积分车辆、轨道与轮对响应…"):
                    result=_run(case,s.get("wr_accuracy","standard"))
            except ValueError as exc:
                st.error(str(exc))
                return
            s["wr_latest"]=(case,result)
            _viewer(result,"wr_vehicle_view")
            st.caption("三维轨道使用参与求解的线路和左右不平顺。姿态及矢量来自同一输出帧。")
            _performance(result)
            st.button("将轮轨工况交给 Copilot",on_click=navigate,args=(COPILOT,),kwargs={"cp_wr_case":case})
