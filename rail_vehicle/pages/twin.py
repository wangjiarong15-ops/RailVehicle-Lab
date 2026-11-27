"""Digital-twin observation, optimization and approved research in Streamlit."""

from dataclasses import replace
import json
import re
import sqlite3

import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.coupled_vehicle import TRACKS, case_config, playback, read_case, simulate
from rail_vehicle.pages.bogie_link import batch_replay_button
from rail_vehicle.replay import ReplayState
from rail_vehicle.run_data import list_vehicle_batches
from rail_vehicle.twin_data import (CHANNELS, COLUMNS, SOURCES, check_case, compare, fuse, historic_samples,
                                    observation_template, parse_observations, quality)
from rail_vehicle.twin_identification import VARIABLES, coefficients
from rail_vehicle.twin_monitoring import residual_profile, suspension_hypotheses
from rail_vehicle.twin_optimization import OBJECTIVES, STRATEGIES, OptimizationPlan, optimize, replay_cases
from rail_vehicle.twin_workflow import (DEMO_RECIPE, STAGES, Request, Research, demo_plan, demonstration, history,
                                        read_research, rerun_request, research_pdf, save_research)
from rail_vehicle.ui import COPILOT, TWIN, navigate, page_title, plot_chart
from rail_vehicle.vehicle_data import list_vehicles
from rail_vehicle.virtual_trials import json_text


@st.cache_data(max_entries=1,show_spinner=False)
def _demo():
    return demonstration()


@st.cache_data(max_entries=12,show_spinner=False)
def _response(case,step=5):
    return simulate(case,substeps=step,include_modes=False)


def proposal_prompt(text):
    return (any(x in text for x in ("参数辨识","校准","标定动力学"))
            and any(x in text for x in ("优化","降低","复算","试验","报告","模型")))


def prompt_defaults(text, plan):
    """Read an explicit, unit-labelled subset and leave the form editable."""
    names={"secondary_y_k_n_m":r"二系(?:横向)?刚度",
           "secondary_y_c_ns_m":r"二系(?:横向)?阻尼",
           "antiroll_k_nm_rad":r"(?:等效)?抗侧滚刚度"}
    bounds=dict(plan.bounds)
    for key,label in names.items():
        unit={"secondary_y_k_n_m":r"kN/m","secondary_y_c_ns_m":r"kN[·*]s/m","antiroll_k_nm_rad":r"kN[·*]m/rad"}[key]
        interval=rf"{label}\s*(?:范围(?:为)?|为|[:：])?\s*(\d+(?:\.\d+)?)\s*(?:至|到|[～~–—-])\s*(\d+(?:\.\d+)?)"
        found=re.search(rf"{interval}\s*{unit}(?![a-zA-Z/²^])",text,re.I)
        if not found and re.search(interval,text,re.I):
            raise ValueError(f"{VARIABLES[key][0]}须使用对应参数单位。")
        if found:
            bounds[key]=tuple(1000*float(v) for v in found.groups())
    changes={}
    for key,label,unit in (("max_roll_mrad","侧滚","mrad"),("max_travel_mm","行程","mm")):
        found=re.search(rf"{label}(?:峰值)?\s*(?:不超过|上限(?:为)?|<=|≤)\s*(\d+(?:\.\d+)?)\s*{unit}",text)
        if found:
            changes[key]=float(found[1])
    return replace(plan,bounds=bounds,**changes)


def _viewer(result,key,other=None,observations=None):
    if not webgl_available():
        st.info("本地三维组件不可用，可继续查看数值及证据。")
        return
    payload = playback(result,gain=2)
    payload["twin"] = True
    if observations:
        names = {"body_y_accel":"accel_m_s2","body_z_accel":"body_z_accel_m_s2","body_roll":"roll_rad",
                 "secondary_front_y":"secondary_y_m"}
        aligned = fuse(result["time_s"],observations,names.keys() & observations.channels.keys())
        payload["observed"] = {names[name]:values for name,values in aligned.items()}
        payload["parameters"]["observation"] = [observations.csv_sha256,observations.metadata_sha256]
    if other:
        alternate = playback(other,gain=2)
        payload["parameters"]["alternative"] = other["evidence_id"]
        payload["coupled"]["display_gain"] = min(payload["coupled"]["display_gain"],alternate["coupled"]["display_gain"])
        payload["alternative"] = alternate
    render_webgl(mode="正常运行",phase=0,state=ReplayState(),visible=PARTS,highlight=(),view="整体视图",
                 focus=None,labels=False,rails=True,exploded=False,spread=0,camera_revision=0,continuous=False,
                 simulation=payload,height=565,key=key)


def _observation_view(case,data):
    st.caption(f"{SOURCES[data.source]} · {data.record} · 时间对齐依据 {data.alignment_record}")
    st.dataframe([{"通道":CHANNELS[key][0],"样本":q["samples"],"估算 Hz":q["inferred_hz"],"长空档":q["gaps"],
                   "相对间隔偏差 p95":q["jitter_fraction_p95"],"配置版本":series.metadata["version"]}
                  for key,series in data.channels.items() if (q:=quality(series))],hide_index=True)
    try:
        check_case(data,case)
    except ValueError as exc:
        st.info(str(exc))
        return
    result = _response(case)
    paired = compare(data,result)
    st.subheader("观测与计算回放")
    _viewer(result,"twin_observed_view",observations=data)
    st.caption("蓝点为原观测时刻的记录，缺测保持为空。姿态由模型计算驱动。")
    st.dataframe([{"通道":CHANNELS[k][0],"SI 单位":CHANNELS[k][1],**row["metrics"]}
                  for k,row in paired["channels"].items()],hide_index=True)
    with st.expander("残差回查与原始行号"):
        channel = st.selectbox("残差通道",list(paired["channels"]),format_func=lambda k:CHANNELS[k][0])
        points = paired["channels"][channel]
        residual = [est-observed for est,observed in zip(points["calculated"],points["observed"],strict=True)]
        fig = go.Figure(go.Scatter(x=points["time_s"],y=residual,mode="markers",customdata=points["csv_lines"],
                                   hovertemplate="%{x:.3f} s · CSV %{customdata} 行 · %{y:.6g}<extra></extra>"))
        fig.update_layout(height=280,xaxis_title="共同相对时间（s）",yaxis_title=f"计算减观测（{CHANNELS[channel][1]}）")
        plot_chart(fig,key="twin_residual")
        st.download_button("下载观测与模型证据",json_text(paired),"paired-observations.json","application/json",on_click="ignore")
        threshold=st.number_input("绝对残差复核阈值（通道 SI 单位）",.000001,100.,.01,format="%.6f",key=f"twin_limit_{channel}")
        review=residual_profile(points,threshold)
        st.caption(f"自定义复核阈值 · {len(review['flagged_csv_lines'])} 条样本超过阈值 · "
                   f"峰值位于 {review['peak']['time_s']:.3f} s，CSV 第 {review['peak']['csv_line']} 行。")
        st.dataframe(review["windows"],hide_index=True)
        if review["spectrum"]:
            spectrum=review["spectrum"]
            fig=go.Figure(go.Scatter(x=spectrum["frequency_hz"],y=spectrum["psd"],mode="lines"))
            fig.update_layout(height=220,xaxis_title="频率（Hz）",yaxis_title="残差功率谱密度（SI²/Hz）")
            plot_chart(fig,key="twin_residual_psd")
        else:
            st.caption(review["spectrum_exclusion"])
        hypothesis_key=(data.csv_sha256,data.metadata_sha256,result["evidence_id"],channel)
        if st.button("复算刚度与阻尼退化假设"):
            st.session_state["twin_hypotheses"]=(hypothesis_key,suspension_hypotheses(case,data,channel))
        saved=st.session_state.get("twin_hypotheses")
        if saved and saved[0]==hypothesis_key:
            hypotheses=saved[1]
            st.caption("教学假设。残差接近可以用于后续检查，仍需实际部件与工况证据。")
            st.dataframe([{"假设":r["scenario"],"线性接触筛查":r["contact_check"],**r["residual"]}
                          for r in hypotheses["scenarios"]],hide_index=True)
            st.download_button("下载假设复算证据",json_text(hypotheses),"twin-residual-hypotheses.json",
                               "application/json",on_click="ignore")


def _history_batch():
    vehicles=list_vehicles()
    if not vehicles:
        st.info("先在车辆管理建档，并导入运行数据。")
        return
    by_id={v["id"]:v["vehicle_code"] for v in vehicles}
    vehicle=st.selectbox("历史车辆",list(by_id),format_func=by_id.get)
    batches={b["id"]:b["file_name"] for b in list_vehicle_batches(vehicle)}
    if not batches:
        st.info("此车辆没有运行批次。")
        return
    batch=st.selectbox("历史批次",list(batches),format_func=batches.get)
    dataset=historic_samples(vehicle,batch)
    st.caption(f"{SOURCES.get(dataset['source'], 'CSV 记录 · 来源待核验')} · UTC 起点 {dataset['clock_origin_utc']} "
               f"· 重复时刻 {dataset['duplicate_timestamps']} · 时基沿用源批次")
    if dataset["truncated"]:
        st.info("当前载入前 6000 条记录，完整批次可在运行分析复核。")
    st.info("普通运行 CSV 缺少首轴激励和时钟映射，当前可以查看观测历史。模型校准需另行绑定配对记录。")
    index=st.slider("历史采样点",1,len(dataset["samples"]),1,key=f"twin_history_{batch}")-1
    s=dataset["samples"][index]
    t=dataset["time_s"]
    cols=st.columns(4)
    for col,label,val in zip(cols,("相对时间","速度","横向加速度","垂向加速度"),
                             (f"{t[index]:.3f} s",f"{s['speed_kmh']:.3g} km/h",
                              f"{s['lateral_accel']:.3g} m/s²",f"{s['vertical_accel']:.3g} m/s²"),strict=True):
        col.metric(label,val)
    st.caption(f"CSV 行 {s['csv_line'] or '历史未登记'} · 数据库样本 {s['id']}")
    fig=go.Figure()
    for key,label in (("lateral_accel","横向"),("vertical_accel","垂向")):
        fig.add_scatter(x=t,y=[row[key] for row in dataset["samples"]],name=label,mode="lines")
    fig.add_vline(x=t[index])
    fig.update_layout(height=300,yaxis_title="加速度（m/s²）",xaxis_title="相对时间（s）")
    plot_chart(fig,key="twin_history_plot")
    st.write("登记传感器与配置版本")
    st.dataframe([{"通道":key,"序列号":row["serial"],"版本":row["version"],"部件":row["metadata"].get("part_id")}
                  for key,row in dataset["sensors"].items()],hide_index=True)
    batch_replay_button(st,label="在三维工作台回放历史批次",key="twin_historical_3d",vehicle_id=vehicle,batch_id=batch)
    with st.expander("批次溯源与采样复核"):
        st.json({key:dataset[key] for key in ("batch_id","csv_sha256","quality","duplicate_timestamps")})


def _files(case):
    with st.expander("工程配对记录",expanded=True):
        st.caption("请提供同车的辨识与独立验证记录，分别登记共同触发时标、传感器版本及激励摘要。")
        st.download_button("观测 CSV 表头",",".join(COLUMNS)+"\n","twin-observations.csv","text/csv",on_click="ignore")
        st.download_button("下载观测元数据模板",json_text(observation_template(case)),
                           "twin-observation-metadata.json","application/json",on_click="ignore")
        a,b=st.columns(2)
        with a:
            first=st.file_uploader("辨识观测 CSV",type="csv",key="tw_train_csv",max_upload_size=2)
            first_meta=st.file_uploader("辨识元数据 JSON",type="json",key="tw_train_meta",max_upload_size=1)
            first_case=st.file_uploader("辨识动力学工况 JSON",type="json",key="tw_train_case",max_upload_size=1)
        with b:
            second=st.file_uploader("独立验证 CSV",type="csv",key="tw_val_csv",max_upload_size=2)
            second_meta=st.file_uploader("验证元数据 JSON",type="json",key="tw_val_meta",max_upload_size=1)
            second_case=st.file_uploader("验证动力学工况 JSON",type="json",key="tw_val_case",max_upload_size=1)
        for title,uploaded in (("辨识",first_case),("验证",second_case)):
            if uploaded is not None:
                try:
                    attached=read_case(uploaded.getvalue())
                except ValueError:
                    continue
                st.download_button(f"下载与{title}工况绑定的元数据模板",json_text(observation_template(attached)),
                                   f"{title}-observation-metadata.json","application/json",on_click="ignore")
        if st.button("核对并绑定观测",type="primary"):
            if not all(item is not None for item in (first,first_meta,first_case,second,second_meta,second_case)):
                st.warning("须提供两份记录的 CSV、元数据和动力学工况。")
            else:
                try:
                    for item in (first_meta,second_meta):
                        if item.size > 32768:
                            raise ValueError("元数据上限 32 KiB。")
                    base,held=(read_case(f.getvalue()) for f in (first_case,second_case))
                    train=parse_observations(first.getvalue(),json.loads(first_meta.getvalue()))
                    validation=parse_observations(second.getvalue(),json.loads(second_meta.getvalue()))
                except (ValueError,UnicodeError,RecursionError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state["twin_observed_input"]=(base,train,held,validation)
        if "twin_observed_input" in st.session_state:
            st.button("解除配对记录绑定",on_click=st.session_state.pop,args=("twin_observed_input",None))


def _plan_form(case, key, label):
    default=demo_plan(case)
    task=key.startswith("twin_task_config")
    if task and (prompt:=st.session_state.get("twin_prompt")):
        try:
            default=prompt_defaults(prompt,default)
        except ValueError:
            st.warning("对话中的范围或约束超出模型支持范围，请在表单中明确填写。")
        st.caption("审批以此表单中显示的范围、单位、约束与预算为准。")
    variables=st.multiselect("设计变量",list(VARIABLES),default=list(default.bounds),format_func=lambda k:VARIABLES[k][0],key=key+"_variables")
    objectives=st.multiselect("优化目标",list(OBJECTIVES),default=list(default.objectives),format_func=OBJECTIVES.get,key=key+"_objectives")
    with st.form(key,border=True):
        bounds={}
        for name in variables:
            text,low,high=VARIABLES[name]
            old=default.bounds[name]
            a,b=st.columns(2)
            unit="kN·m/rad" if "antiroll" in name else "kN·s/m" if name.endswith("_c_ns_m") else "kN/m"
            bounds[name]=(a.number_input(f"{text}下限（{unit}）",low/1000,high/1000,old[0]/1000,key=key+name+"lo")*1000,
                          b.number_input(f"{text}上限（{unit}）",low/1000,high/1000,old[1]/1000,key=key+name+"hi")*1000)
        weights=tuple(st.number_input(f"{OBJECTIVES[obj]}权重",.01,100.,1.,key=key+obj) for obj in objectives)
        a,b=st.columns(2)
        speeds=a.text_input("速度组合（m/s，逗号分隔）",f"{case.speed_m_s:g}")
        tracks=b.multiselect("轨道激励",["bump","sine","random"]+(["custom"] if case.profile else []),
                             ["bump","sine"],format_func=TRACKS.get)
        a,b=st.columns(2)
        roll=a.number_input("侧滚上限（mrad）",.01,100.,default.max_roll_mrad)
        travel=b.number_input("二系横向行程上限（mm）",.01,200.,default.max_travel_mm)
        a,b=st.columns(2)
        strategy=a.selectbox("搜索策略",list(STRATEGIES),index=3,format_func=STRATEGIES.get)
        budget=b.number_input("优化求解次数预算",12,160,default.budget)
        id_budget=84
        if task:
            id_budget=st.number_input("辨识求解次数预算",24,180,84)
        submitted=st.form_submit_button(label,type="primary")
    if submitted:
        try:
            return OptimizationPlan(bounds,tuple(float(x.strip()) for x in speeds.replace("，",",").split(",")),
                                    tuple(tracks),tuple(objectives),weights,roll,travel,budget,strategy),id_budget
        except ValueError as exc:
            st.warning(str(exc))
    return None


def _optimization_result(report,key):
    st.caption(f"{report['evaluations']} 次模型求解 · {report['evidence_id']} · 指标为各工况最不利值")
    front=report["pareto"]
    if not front:
        st.warning("当前预算内没有复算通过的方案。可调整参数范围或检查约束。")
    else:
        objectives=report["plan"]["objectives"]
        selected=st.selectbox("半步长复算 Pareto 方案",range(len(front)),format_func=lambda i:f"方案 {i+1}",key=key+"_front")
        st.dataframe([{"方案":"基准",**{OBJECTIVES[o]:report["baseline"]["worst"][o] for o in objectives}},
                      *({"方案":f"方案 {i+1}",**{OBJECTIVES[o]:row["worst"][o] for o in objectives}} for i,row in enumerate(front))],
                     hide_index=True)
        if len(objectives)>1:
            x,y=objectives[:2]
            fig=go.Figure(go.Scatter(x=[r["worst"][x] for r in front],y=[r["worst"][y] for r in front],
                                     name="复算可行方案",mode="markers",text=[f"方案 {i+1}" for i in range(len(front))]))
            fig.add_scatter(x=[report["baseline"]["worst"][x]],y=[report["baseline"]["worst"][y]],
                            name="基准",mode="markers",marker=dict(symbol="diamond",size=9))
            fig.update_layout(height=255,xaxis_title=OBJECTIVES[x],yaxis_title=OBJECTIVES[y])
            plot_chart(fig,key=key+"_pareto")
        labels=[f"{row['speed_m_s']:g} m/s · {TRACKS[row['track']]}" for row in front[selected]["conditions"]]
        condition=st.selectbox("三维复算工况",range(len(labels)),format_func=labels.__getitem__,key=key+"_condition")
        case=read_case(json_text(report["case"]))
        base,after=replay_cases(case,report,condition=condition,candidate=selected)
        _viewer(_response(base,10),key+"_dual_view",other=_response(after,10))
        st.caption("左右三维与曲线共享一个采样时刻。着色表示二系横向计算行程。")
    if report["sensitivity"]:
        with st.expander("参数灵敏度排序"):
            st.dataframe([{"变量":VARIABLES[row["variable"]][0],"归一化区间割线斜率":row["score_span"]}
                          for row in report["sensitivity"]],hide_index=True)
    st.download_button("下载优化及复算证据",json_text(report),"twin-optimization.json","application/json",key=key+"_json",on_click="ignore")


@st.fragment(run_every=.5)
def _task_progress(job):
    status,stage,error,report=job.state()
    if status in ("complete","cancelled","error"):
        st.rerun()
    st.progress(stage/len(STAGES),text=f"{'已暂停' if status=='paused' else '正在执行'} · {STAGES[min(stage,len(STAGES)-1)]}")
    a,b=st.columns(2)
    if status=="paused":
        a.button("继续工程任务",on_click=job.start)
    else:
        a.button("暂停工程任务",on_click=job.pause,disabled=status!="running")
    b.button("取消工程任务",on_click=job.cancel,disabled=status=="cancelling")


def _research_report(report,public):
    fit=report["identification"]
    st.subheader("工程任务结果")
    st.caption(f"{SOURCES[report['source']]} · {report['evidence_id']}")
    if fit["identified"]:
        st.success(f"独立验证归一化 RMSE {fit['baseline_normalized_rmse']:.4g} → {fit['validation_normalized_rmse']:.4g}")
        st.dataframe([{"参数":VARIABLES[k][0],"估计 SI":v,"局部 95% 下限":fit["conditional_95_interval"][k][0],
                       "局部 95% 上限":fit["conditional_95_interval"][k][1]} for k,v in fit["identified"].items()],hide_index=True)
        st.caption("区间使用局部线性化和 i.i.d. 残差假设，整车参数与外部激励等固定输入的误差未计入。")
    else:
        st.warning("当前记录不足以可靠辨识。请核对输入激励、观测变化、候选范围和独立验证误差。")
    with st.expander("辨识质量与独立验证证据"):
        st.json({k:fit[k] for k in ("rank","condition","flags","evaluations","validation_baseline","validation_fitted")})
    if report["optimization"]:
        _optimization_result(report["optimization"],"twin_research")
    a,b,c=st.columns(3)
    a.download_button("下载工程证据 JSON",json_text(report),"twin-research.json","application/json",on_click="ignore")
    b.download_button("下载工程任务 PDF",research_pdf(report),"twin-research.pdf","application/pdf",on_click="ignore")
    if not public and c.button("归档工程任务"):
        try:
            save_research(report)
        except (ValueError,sqlite3.Error) as exc:
            st.error(str(exc))
        else:
            st.success("工程任务已归档。")


def _task(public,data):
    state=st.session_state
    st.markdown(" → ".join(STAGES))
    st.caption("本地白名单执行。先核对资料与计算预算，再批准运行。")
    if prompt:=state.get("twin_prompt"):
        st.text(prompt)
    job=state.get("twin_job")
    if job:
        status,stage,error,report=job.state()
        if status in ("running","paused","cancelling"):
            _task_progress(job)
            return
        if status=="draft":
            st.info(f"等待批准 · 观测基准 1 次 · 辨识最多 {job.request.identification_budget} 次 · 优化最多 {job.request.plan.budget} 次")
            st.caption(f"{SOURCES[job.request.observations.source]} · {job.request.observations.vehicle_ref} · "
                       f"{job.request.observations.record} / {job.request.validation.record}")
            st.dataframe([{"变量":VARIABLES[k][0],"下限 SI":lo,"上限 SI":hi}
                          for k,(lo,hi) in job.request.plan.bounds.items()],hide_index=True)
            st.caption(f"目标 {'、'.join(OBJECTIVES[o] for o in job.request.plan.objectives)} · "
                       f"{len(job.request.plan.speeds)*len(job.request.plan.tracks)} 个激励与速度组合 · "
                       f"侧滚上限 {job.request.plan.max_roll_mrad:g} mrad · 行程上限 {job.request.plan.max_travel_mm:g} mm")
            a,b=st.columns(2)
            a.button("批准并执行工程任务",on_click=job.start,type="primary")
            b.button("返回调整计划",on_click=state.pop,args=("twin_job",None))
            return
        if status=="error":
            st.error(error)
            st.button("重试当前阶段",on_click=job.start)
        if status=="cancelled":
            st.info("工程任务已取消。")
        if status=="complete":
            expected=state.get("twin_expected")
            if expected:
                if report["evidence_id"]==expected:
                    st.success("归档复算摘要一致。")
                else:
                    st.warning("软件或数值环境变化，复算已生成新的摘要。")
            _research_report(report,public)
        st.button("新建工程任务",on_click=state.pop,args=("twin_job",None))
        return
    if not data:
        st.info("从「观测与回放」绑定两份工程记录，或显式选择虚构试验。")
    if not public:
        with st.expander("历史工程任务"):
            records=dict(history())
            if records:
                evidence=st.selectbox("选择归档",list(records),format_func=lambda k:f"{records[k]} UTC · {k[9:21]}")
                if st.button("恢复归档并准备复算"):
                    try:
                        archive=read_research(evidence)
                        current=Request(*data,demo_plan(data[0])) if data else None
                        state["twin_job"]=Research(rerun_request(archive,current))
                        state["twin_expected"]=evidence
                        st.rerun()
                    except (ValueError,sqlite3.Error) as exc:
                        st.warning(str(exc))
            else:
                st.caption("暂无已归档任务。")
    if not data:
        return
    base,obs,held,validation=data
    st.caption(f"{SOURCES[obs.source]} · 训练 {obs.record} · 验证 {validation.record}")
    choice=_plan_form(base,f"twin_task_config_{state.get('twin_prompt_rev',0)}","生成可审查计划")
    if choice:
        plan,id_budget=choice
        recipe=DEMO_RECIPE if state["twin_source"]=="虚构试验" else None
        state.pop("twin_expected",None)
        state["twin_job"]=Research(Request(base,obs,held,validation,plan,id_budget,recipe))
        st.rerun()


def render(public):
    page_title("数字孪生工作台","对齐车辆观测与动力学计算，校准参数并复算改进方案。")
    s=st.session_state
    s.setdefault("twin_source","虚构试验")
    s.setdefault("twin_section","观测与回放")
    choices=["虚构试验"] if public else ["虚构试验","工程记录","历史批次"]
    if s["twin_source"] not in choices:
        s["twin_source"]="虚构试验"
    source=st.radio("记录来源",choices,key="twin_source",horizontal=True)
    section=st.radio("工作区",("观测与回放","工程优化中心","审批与任务"),key="twin_section",horizontal=True)
    demonstration_case,(demo,*files),held,(verification,*validation_files)=_demo()
    current=(demonstration_case,demo,held,verification) if source=="虚构试验" else s.get("twin_observed_input") if source=="工程记录" else None
    if section=="审批与任务":
        _task(public,current)
        return
    if section=="工程优化中心":
        if current is None:
            st.info("请选择虚构试验，或在观测与回放中绑定工程记录及动力学工况。")
            return
        base=current[0]
        if job:=s.get("twin_job"):
            _,_,_,study=job.state()
            if study and study["identification"]["identified"] and study["identification"]["training"]==(
                    current[1].evidence() if current else None):
                if st.checkbox("以已复核的辨识参数为基准",value=True):
                    base=coefficients(base,study["identification"]["identified"])
        st.subheader("工程优化中心")
        st.caption("目标和约束按教学模型计算。垂向 RMS 可作联合目标，当前横向变量不会改变垂向子系统。")
        plan=_plan_form(base,"twin_opt","运行有界优化")
        if plan:
            s.pop("twin_opt_result",None)
            try:
                with st.spinner("正在计算候选并独立复算…"):
                    s["twin_opt_result"]=(base,optimize(base,plan[0]))
            except ValueError as exc:
                st.warning(str(exc))
        if (cached:=s.get("twin_opt_result")) and cached[0]==base:
            _optimization_result(cached[1],"twin_optimization")
        return
    if source=="历史批次":
        try:
            _history_batch()
        except (ValueError,sqlite3.Error) as exc:
            st.warning(str(exc))
        return
    if source=="工程记录":
        _files(demonstration_case)
        current=s.get("twin_observed_input")
    else:
        st.caption("离线虚构演示 · 观测带噪声和已知时钟偏差 · 参考参数不代表实车。")
        with st.expander("下载虚构原始记录"):
            for key,items,config in (("辨识",files,demonstration_case),("验证",validation_files,held)):
                st.download_button(key+"观测 CSV",items[0],key+"-synthetic.csv","text/csv",on_click="ignore")
                st.download_button(key+"元数据 JSON",json_text(items[1]),key+"-synthetic.json","application/json",on_click="ignore")
                st.download_button(key+"动力学工况 JSON",json_text(case_config(config)),key+"-case.json",
                                   "application/json",on_click="ignore")
    if current:
        _observation_view(current[0],current[1])
        st.button("将当前观测交给 Copilot",on_click=navigate,args=(COPILOT,),kwargs={"cp_twin_source":source})
