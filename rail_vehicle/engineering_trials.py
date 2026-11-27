"""Bounded, versioned 31-DOF trials with durable, evidence-bound requests."""

from dataclasses import dataclass, replace
from hashlib import sha256
from io import BytesIO
import json
from threading import BoundedSemaphore

import numpy as np

from rail_vehicle.copilot.knowledge import search
from rail_vehicle.copilot.tools import events, quality, statistics
from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.event_anchor import resolve_event_row
from rail_vehicle.nonlinear_vehicle import MODEL, WheelRailCase, case_config, performance, read_case, run_vehicle
from rail_vehicle.replay import prepare_replay
from rail_vehicle.twin_workflow import Research
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range
from rail_vehicle.wheel_track import template

SCHEMA="rail.engineering-trial.v1"
PHASES=("计算批准的基准与对照","独立复算","核对证据与归档")
PARAMETERS={
    "speed":("运行速度","m/s",5.,55.),
    "load":("车体质量比例","倍",.75,1.25),
    "primary_k":("一系横向刚度比例","倍",.6,1.3),
    "secondary_k":("二系横向刚度比例","倍",.6,1.3),
    "secondary_c":("二系横向阻尼比例","倍",.6,1.3),
    "radius":("曲线半径","m",125.,2000.),
    "cant":("超高","mm",-120.,120.),
    "irregularity":("不平顺幅值比例","倍",0.,1.5),
}
HYPOTHESES=(("primary_k",.85),("secondary_k",.85),("secondary_c",.75))
_WORKER=BoundedSemaphore(1)


def _digest(value):
    return sha256(json_text(value).encode()).hexdigest()


def lateral_intent(text):
    return ("横向" in text and any(word in text for word in ("对照","三组","试验","仿真"))
            and ("异常" in text or ("三组" in text and "悬挂" in text)))


def lateral_evidence(work):
    if work.data is None:
        raise ValueError("请从运行分析绑定车辆及批次，或在 Copilot 选择内置虚构批次。")
    data=work.data
    found=events(work,"lateral").events
    if not found:
        raise ValueError("当前窗口没有横向超阈值区段，可核对阈值或使用独立参数试验。")
    selected=next((event for event in found if event["evidence_id"]==data.preferred_event),None)
    event=selected or max(found,key=lambda e:e["maximum_absolute_value"])
    track=prepare_replay(data.rows)
    start=resolve_event_row(track,event["start_timestamp"],"lateral",data.threshold,anchor=event["anchor"])
    if start is None:
        raise ValueError("事件与当前数据指纹不一致，无法定位。")
    end=start+event["sample_count"]-1
    first,last=track.samples[start],track.samples[end]
    sampling=quality(work)
    stats=statistics(work,"lateral")
    return {"dataset_sha256":data.identity,"source":data.source,"source_label":data.source_description,
            "vehicle_code":data.vehicle["vehicle_code"],"batch":data.batch["file_name"],
            "threshold_m_s2":data.threshold,"event_id":event["evidence_id"],
            "event_start":event["start_timestamp"],"event_end":event["end_timestamp"],
            "index_1based":[start+1,end+1],"csv_lines":[first.get("csv_line"),last.get("csv_line")],
            "samples":event["sample_count"],"peak_m_s2":event["maximum_absolute_value"],
            "median_event_speed_kmh":float(np.median([sample["speed_kmh"] for sample in track.samples[start:end+1]])),
            "sample_quality":sampling.text,"window_metrics":stats.text,
            "selection":"current_bound_event" if selected else "max_abs_lateral_threshold_event",
            "anchor_sha256":event["anchor"].fingerprint.hex()}


def vary(base,parameter,value):
    _,_,minimum,maximum=PARAMETERS[parameter]
    if not in_range(value,minimum,maximum):
        raise ValueError("对照参数超出本机实验区间。")
    if parameter=="speed":
        return replace(base,speed_m_s=value)
    if parameter=="load":
        return replace(base,vehicle=replace(base.vehicle,body_mass_kg=base.vehicle.body_mass_kg*value,
                                            body_inertia_kg_m2=base.vehicle.body_inertia_kg_m2*value))
    if parameter in ("primary_k","secondary_k","secondary_c"):
        field={"primary_k":"primary_lateral_k_scale","secondary_k":"secondary_lateral_k_scale",
               "secondary_c":"secondary_lateral_c_scale"}[parameter]
        return replace(base,**{field:getattr(base,field)*value})
    route=base.route
    if parameter=="irregularity":
        profile=tuple((station,shift*value,left*value,right*value) for station,shift,left,right in route.profile)
        return replace(base,route=replace(route,profile=profile,align_m=route.align_m*value,
                                          left_m=route.left_m*value,right_m=route.right_m*value))
    peak=max(abs(section.end_curvature_m_inv) for section in route.sections)
    if not peak and parameter=="cant":
        raise ValueError("超高扫描需要曲线工况。请先设置曲线。")
    if parameter=="radius":
        if not peak:
            curve=template("curve",value)
            return replace(base,route=replace(route,sections=curve.sections))
        stretches=tuple(replace(s,end_curvature_m_inv=s.end_curvature_m_inv/(peak*value)) for s in route.sections)
    else:
        stretches=tuple(replace(s,end_cant_m=value*s.end_curvature_m_inv/(1000*peak)) for s in route.sections)
    return replace(base,route=replace(route,sections=stretches))


@dataclass(frozen=True)
class Trial:
    base: WheelRailCase
    changes: tuple
    travel_mm: float=35.
    observation: dict|None=None

    def __post_init__(self):
        if not isinstance(self.base,WheelRailCase) or not in_range(self.travel_mm,4,100):
            raise ValueError("基准工况或二系垂向行程约束无效。")
        if not 1<=len(self.changes)<=3 or len(set(self.changes))!=len(self.changes):
            raise ValueError("请选择一至三项不同的对照。")
        configurations=set()
        for name,value in self.changes:
            if name not in PARAMETERS:
                raise ValueError("无法识别所选工程参数。")
            alternative=vary(self.base,name,value)
            if alternative==self.base or alternative in configurations:
                raise ValueError("参数矩阵含重复或与基准相同的工况。")
            configurations.add(alternative)
        if self.observation is not None:
            required={"dataset_sha256","source_label","vehicle_code","event_id","event_start",
                      "peak_m_s2","index_1based","csv_lines"}
            if (not isinstance(self.observation,dict) or not required.issubset(self.observation)
                    or len(json_text(self.observation))>6000):
                raise ValueError("事件证据缺少来源字段或超出归档范围。")

    def payload(self):
        content={"schema":SCHEMA,"model":MODEL,"base":case_config(self.base),"changes":self.changes,
                 "secondary_travel_mm":self.travel_mm,"observation":self.observation,
                 "budget":len(self.changes)+3}
        return {**content,"trial_id":"trial."+_digest(content)}


def read_request(value):
    try:
        request=Trial(read_case(json_text(value["base"])),tuple(tuple(row) for row in value["changes"]),
                      value["secondary_travel_mm"],value["observation"])
        if json_text(request.payload())!=json_text(value):
            raise ValueError
    except (TypeError,KeyError,ValueError,RecursionError):
        raise ValueError("试验版本或请求摘要无法核对。") from None
    return request


def check_binding(request,data):
    if request.observation is not None and (data is None or request.observation["dataset_sha256"]!=data.identity):
        raise ValueError("当前数据与批准计划的事件快照不同。请重新绑定原车辆、批次和筛选范围。")


def solve_trial(request, *, checkpoint=lambda:None, stage=lambda _:None, advance=lambda _:None, capture=None):
    count=0
    payload=request.payload()

    def run(case, name, parameter, value, accuracy="standard"):
        nonlocal count
        checkpoint()
        count+=1
        advance(count)
        row={"name":name,"parameter":parameter,"value":value,"case":case_config(case)}
        try:
            response=run_vehicle(case,accuracy=accuracy,checkpoint=checkpoint)
        except ValueError as exc:
            return {**row,"error":str(exc),"violations":["solver_domain"]}
        metrics=performance(response)
        index=int(np.argmax(np.abs(np.asarray(response["ddq"])[:,0])))
        violations=[]
        if metrics["separated_samples"] or metrics["linear_limit_samples"]:
            violations.append("contact_domain")
        if metrics["secondary_travel_peak_mm"]>request.travel_mm:
            violations.append("secondary_travel")
        row.update(metrics=metrics,solver=response["solver"],response_id=response["evidence_id"],
                   peak_time_s=response["time_s"][index],trailing_station_m=response["station_m"][-1][-1],
                   violations=violations)
        if capture:
            capture(response)
        return row

    stage(0)
    base=run(request.base,"共同基准",None,None)
    alternatives=[run(vary(request.base,name,value),PARAMETERS[name][0],name,value) for name,value in request.changes]
    stages={item["parameter"] for item in alternatives}
    if len(stages)==1 and len(alternatives)>=2 and all("metrics" in item for item in alternatives):
        left,right=sorted(alternatives,key=lambda item:item["value"])[::len(alternatives)-1]
        sensitivity=(right["metrics"]["body_y_rms_m_s2"]-left["metrics"]["body_y_rms_m_s2"])/(right["value"]-left["value"])
    else:
        sensitivity=None
    stage(1)
    viable=[row for row in alternatives if not row["violations"]]
    comparison=None
    if not base["violations"] and viable:
        lead=min(viable,key=lambda row:row["metrics"]["body_y_rms_m_s2"])
        param,value=lead["parameter"],lead["value"]
        check0=run(request.base,base["name"],None,None,"independent")
        check1=run(vary(request.base,param,value),lead["name"],param,value,"independent")
        comparison={"baseline":check0,"candidate":check1,"eligible":not(check0["violations"] or check1["violations"])}
        if comparison["eligible"]:
            values=[row["metrics"]["body_y_rms_m_s2"] for row in (base,lead,check0,check1)]
            budget=abs(values[0]-values[2])+abs(values[1]-values[3])
            difference=values[2]-values[3]
            comparison.update(reduction_m_s2=difference,numerical_variation_m_s2=budget,
                              improves_this_model=difference>max(budget,.0001))
    stage(2)
    sources=[{"id":p.id,"title":p.title,"source":p.source} for p in search("悬挂刚度阻尼 受迫振动 横向振动")]
    result={"schema":SCHEMA,"model":MODEL,"request":payload,"comparison":comparison,
            "baseline":base,"cases":alternatives,"solves":count,"sensitivity":sensitivity,
            "sources":sources,"scope":"measurement snapshot and independent simulated hypotheses",
            "model_limits":"bounded 31-DOF experimental model; no inferred defect or field calibration"}
    return {**result,"evidence_id":"engineering."+_digest(result)}


def save_request(request,db_path=DB_PATH):
    payload=request.payload()
    with get_connection(db_path) as connection:
        connection.execute("INSERT OR IGNORE INTO engineering_trial_records(trial_id,request_json) VALUES (?,?)",
                           (payload["trial_id"],json_text(payload)))


def _verified(report, trial_id):
    body=dict(report)
    evidence=body.pop("evidence_id")
    if evidence!="engineering."+_digest(body) or read_request(body["request"]).payload()["trial_id"]!=trial_id:
        raise ValueError("报告摘要与已批准计划不一致。")
    return report


def load_trial(trial_id,db_path=DB_PATH):
    with get_connection(db_path) as connection:
        row=connection.execute("SELECT request_json,report_json FROM engineering_trial_records WHERE trial_id=?",
                               (trial_id,)).fetchone()
    if row is None:
        raise ValueError("未找到该本地试验。")
    request=read_request(json.loads(row["request_json"]))
    if request.payload()["trial_id"]!=trial_id:
        raise ValueError("保存的试验指纹不一致。")
    return request,_verified(json.loads(row["report_json"]),trial_id) if row["report_json"] else None


def history(db_path=DB_PATH):
    with get_connection(db_path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT trial_id,created_at,report_json IS NOT NULL AS finished "
            "FROM engineering_trial_records ORDER BY created_at DESC,rowid DESC LIMIT 20")]


class TrialJob(Research):
    active_budget_s=180

    def __init__(self, request, db_path=DB_PATH):
        super().__init__(request)
        self.db_path=db_path
        self.responses={}
        self.started_solves=0

    def _run(self):
        if not _WORKER.acquire(blocking=False):
            with self._condition:
                self.status,self.error="error","本机已有一项参数试验，可稍后重试。"
            return
        try:
            traces={}

            def progress(stage):
                with self._condition:
                    self.stage=stage

            def count(value):
                with self._condition:
                    self.started_solves=value

            report=solve_trial(self.request,checkpoint=self._checkpoint,stage=progress,advance=count,
                               capture=lambda response:traces.update({response["evidence_id"]:response}))
            self._checkpoint()
            with self._condition:
                if self.status=="cancelling":
                    raise InterruptedError
                trial_id=self.request.payload()["trial_id"]
                _verified(report,trial_id)
                with get_connection(self.db_path) as connection:
                    cursor=connection.execute("UPDATE engineering_trial_records SET report_json=? WHERE trial_id=?",
                                              (json_text(report),trial_id))
                    if cursor.rowcount!=1:
                        raise ValueError("审批记录缺失，试验未归档。")
                self.responses,self.report,self.status,self.stage=traces,report,"complete",len(PHASES)
        except InterruptedError:
            with self._condition:
                self.status="cancelled"
        except (ValueError,TimeoutError) as exc:
            with self._condition:
                self.status,self.error="error",str(exc)
        except Exception:
            with self._condition:
                self.status,self.error="error","本地计算或保存失败，已批准的配置可从历史任务恢复。"
        finally:
            _WORKER.release()


def trial_report_pdf(report):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.pagesizes import A4
    from rail_vehicle.analysis_report import _font_names

    out=BytesIO()
    pdf=Canvas(out,pagesize=A4)
    regular,bold=_font_names()
    pdf.setFont(bold,17)
    pdf.drawString(40,790,"RailVehicle-Lab 参数对照与证据")
    pdf.setFont(regular,10)
    context=report["request"]["observation"]
    lines=[report["evidence_id"],f"模型 {MODEL} · 实验性计算 · {report['solves']} 次积分",
           f"本次声明二系行程上限 {report['request']['secondary_travel_mm']:.2f} mm"]
    if context:
        csv_lines=" 至 ".join(str(line) if line is not None else "未记录" for line in context["csv_lines"])
        if all(line is None for line in context["csv_lines"]):
            csv_lines="未记录"
        lines.extend((f"输入记录 {context['source_label']} · {context['vehicle_code']}",
                      f"{context['event_id']} · {context['event_start']} · 峰值 {context['peak_m_s2']:g} m/s^2",
                      f"当前范围样本 {context['index_1based']} · CSV 行 {csv_lines}",
                      context["dataset_sha256"]))
    for row in [report["baseline"],*report["cases"]]:
        value=f" {row['value']:g}" if row["value"] is not None else ""
        if "metrics" in row:
            metrics=row["metrics"]
            lines.append(f"{row['name']}{value} · 横向 RMS {metrics['body_y_rms_m_s2']:.6g} m/s^2"
                         f" · 二系行程 {metrics['secondary_travel_peak_mm']:.3f} mm"
                         f" · {'有约束触发' if row['violations'] else '未触发所设约束'}")
        else:
            lines.append(f"{row['name']}{value} · {row['error']}")
    comparison=report["comparison"]
    if comparison and comparison["eligible"]:
        lines.append(f"RK45 复算：RMS 差值 {comparison['reduction_m_s2']:.6g} m/s^2"
                     f" · 求解器差异包络 {comparison['numerical_variation_m_s2']:.3g} m/s^2")
    elif comparison:
        lines.append("独立复算触发约束，保留对照记录供检查。")
    else:
        lines.append("当前模型工况的约束结果未进入独立复算。")
    lines.extend(("观测异常与数值假设分开列示。机械原因须经同工况独立测量核对。",
                  "原始工况、独立复算、来源及约束明细见同编号 JSON。"))
    y=757
    for line in lines:
        while line:
            limit=len(line)
            while pdf.stringWidth(line[:limit],regular,10)>515:
                limit-=1
            pdf.drawString(40,y,line[:limit])
            line=line[limit:]
            y-=19
    pdf.save()
    return out.getvalue()
