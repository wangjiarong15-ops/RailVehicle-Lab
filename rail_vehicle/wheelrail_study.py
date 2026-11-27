"""Budget-limited multi-curve studies and independently verified evidence."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from io import BytesIO
from itertools import product
import json
from threading import BoundedSemaphore

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from rail_vehicle.analysis_report import _font_names
from rail_vehicle.nonlinear_vehicle import MODEL, WheelRailCase, case_config, performance, read_case, run_vehicle
from rail_vehicle.twin_workflow import Research
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range
from rail_vehicle.wheel_track import template

SCHEMA="rail.wheel-rail.study.v1"
PHASES=("计算对称与退化基准","扫描右二系修复比例","独立 RK45 复算","整理数值与报告")
_STUDIES=BoundedSemaphore(1)


def _axis(values,low,high):
    if not isinstance(values,(list,tuple)) or not 1<=len(values)<=2 or not all(
            in_range(v,low,high) for v in values) or len(set(values))!=len(values):
        raise ValueError(f"各扫描轴需要 1 至 2 个不同的数值，范围 {low:g} 至 {high:g}。")
    return tuple(sorted(map(float,values)))


@dataclass(frozen=True)
class StudyPlan:
    speeds: tuple=(20.,22.)
    radii: tuple=(450.,650.)
    repairs: tuple=(.5,1.)
    right_factor: float=.8
    max_travel_mm: float=33.
    maximum_solves: int=24

    def __post_init__(self):
        for key,lo,hi in (("speeds",10,40),("radii",200,1600),("repairs",.1,1.)):
            object.__setattr__(self,key,_axis(getattr(self,key),lo,hi))
        if not in_range(self.right_factor,.65,.99) or not in_range(self.max_travel_mm,4,100):
            raise ValueError("右二系退化比例或二系行程上限无效。")
        needed=len(self.speeds)*len(self.radii)*(2+len(self.repairs)+2)
        if type(self.maximum_solves) is not int or not needed<=self.maximum_solves<=24:
            raise ValueError(f"该计划至少需 {needed} 次求解，包括保留的独立复算次数。")


def condition_case(base,speed,radius,plan,repair=None):
    if not isinstance(base,WheelRailCase):
        raise ValueError("需要高级轮轨工况。")
    profile=base.route
    cant=profile.sections[-1].end_cant_m
    route=replace(template("curve",radius,cant,profile.left_m,profile.right_m,profile.align_m),
                  profile=profile.profile,wavelength_m=profile.wavelength_m)
    left=base.secondary_left
    factor=1. if repair is None else plan.right_factor+repair*(1-plan.right_factor)
    right=replace(left,stiffness_n_m=left.stiffness_n_m*factor,damping_ns_m=left.damping_ns_m*factor)
    return replace(base,speed_m_s=speed,route=route,secondary_right=right)


def solve_study(base,plan,*,checkpoint=lambda:None,stage=lambda _:None,capture=None):
    scenarios=list(product(plan.speeds,plan.radii))
    if any(speed*base.duration_s < 32 + 2*base.vehicle.bogie_half_spacing_m+2.6 for speed,_ in scenarios):
        raise ValueError("试验时窗不足以让最后一轴完成缓和曲线。请延长时长或提高最低速度。")
    count=0

    def evaluate(repair,accuracy="standard"):
        nonlocal count
        rows=[]
        for speed,radius in scenarios:
            checkpoint()
            if count>=plan.maximum_solves:
                raise RuntimeError("已达到批准的工况预算。")
            case=condition_case(base,speed,radius,plan,repair)
            count+=1
            try:
                result=run_vehicle(case,accuracy=accuracy,checkpoint=checkpoint)
            except ValueError as exc:
                rows.append({"speed_m_s":speed,"radius_m":radius,"error":str(exc)})
                continue
            rows.append({"speed_m_s":speed,"radius_m":radius,"case":case_config(case),
                         "evidence_id":result["evidence_id"],"configuration_sha256":result["configuration_sha256"],
                         "metrics":performance(result),"solver":result["solver"]})
            if accuracy=="independent" and capture is not None:
                capture(result)
        metric=[row["metrics"] for row in rows if "metrics" in row]
        peak=max((p["secondary_travel_peak_mm"] for p in metric),default=None)
        flags=[]
        if len(metric)!=len(rows):
            flags.append("failed_case")
        if any(p["separated_samples"] or p["linear_limit_samples"] for p in metric):
            flags.append("contact_domain")
        if peak is not None and peak>plan.max_travel_mm:
            flags.append("secondary_travel")
        return {"repair":repair,"rows":rows,"violations":flags,"max_travel_mm":peak,
                "worst_body_y_rms_m_s2":max((r["body_y_rms_m_s2"] for r in metric),default=None)}

    stage(0)
    symmetric,degraded=evaluate(None),evaluate(0.)
    stage(1)
    trials=[evaluate(fraction) for fraction in plan.repairs]
    eligible=sorted((trial for trial in trials if not trial["violations"]),
                    key=lambda r:(r["worst_body_y_rms_m_s2"],r["max_travel_mm"],r["repair"]))
    stage(2)
    comparison=None
    if eligible:
        coarse=eligible[0]
        reference,candidate=evaluate(0.,"independent"),evaluate(coarse["repair"],"independent")
        for expected,verified in ((degraded,reference),(coarse,candidate)):
            for a,b in zip(expected["rows"],verified["rows"],strict=True):
                if ("metrics" in a)!=("metrics" in b):
                    verified["violations"].append("independent_disagreement")
                    break
                if "metrics" not in a:
                    continue
                for field,limit in (("secondary_travel_peak_mm",.20),("body_y_rms_m_s2",.025)):
                    if abs(a["metrics"][field]-b["metrics"][field])>limit:
                        verified["violations"].append("independent_disagreement")
                        break
                if "independent_disagreement" in verified["violations"]:
                    break
        comparison={"reference":reference,"candidate":candidate,
                    "qualified":not candidate["violations"] and not any(code in reference["violations"]
                                                                      for code in ("failed_case","independent_disagreement"))}
    stage(3)
    report={"schema":SCHEMA,"model":MODEL,"source":"experimental_teaching","base":case_config(base),
            "plan":asdict(plan),"solves":count,"symmetric":symmetric,"degraded":degraded,
            "trials":trials,"comparison":comparison,"scope":"declared experimental cases, no operating safety claim"}
    return {**report,"evidence_id":"wheelrail.study."+sha256(json_text(report).encode()).hexdigest()}


def restore_study(data):
    if not isinstance(data,(bytes,str)) or len(data)>512_000:
        raise ValueError("试验记录须小于 512 KB。")
    try:
        source=json.loads(data)
        actual=source.pop("evidence_id")
        if (source["schema"]!=SCHEMA or source["model"]!=MODEL
                or actual!="wheelrail.study."+sha256(json_text(source).encode()).hexdigest()):
            raise ValueError
        return read_case(json_text(source["base"])),StudyPlan(**source["plan"]),actual
    except (ValueError,KeyError,TypeError,AttributeError,RecursionError):
        raise ValueError("试验记录版本或 SHA-256 无法核对。") from None


class WheelRailStudy(Research):
    """Use the same cooperative pause/cancel controls as the existing local Copilot."""
    active_budget_s=210

    def __init__(self,request):
        super().__init__(request)
        self._responses={}

    def response(self,evidence_id):
        with self._condition:
            return self._responses.get(evidence_id)

    def _run(self):
        if not _STUDIES.acquire(blocking=False):
            with self._condition:
                self.status,self.error="error","轮轨试验正在运行，可稍后重试。"
            return
        try:
            responses={}

            def capture(result):
                responses[result["evidence_id"]]=result

            def stage(number):
                self._checkpoint()
                with self._condition:
                    self.stage=number
            report=solve_study(*self.request,checkpoint=self._checkpoint,stage=stage,capture=capture)
            self._checkpoint()
            with self._condition:
                if self.status=="cancelling":
                    raise InterruptedError
                if report["comparison"] and report["comparison"]["qualified"]:
                    self._responses=responses
                self.report,self.status,self.stage=report,"complete",len(PHASES)
        except InterruptedError:
            with self._condition:
                self.status="cancelled"
        except (ValueError,TimeoutError) as exc:
            with self._condition:
                self.status,self.error="error",str(exc)
        except Exception:
            with self._condition:
                self.status,self.error="error","轮轨试验中断，可从批准的工况配置重试。"
        finally:
            _STUDIES.release()


def study_pdf(report):
    regular,bold=_font_names()
    stream=BytesIO()
    canvas=Canvas(stream,pagesize=A4)
    canvas.setTitle("RailVehicle-Lab 轮轨曲线试验")
    canvas.setFont(bold,17)
    canvas.drawString(35,797,"RailVehicle-Lab 轮轨曲线试验")
    canvas.setFont(regular,9)
    plan=report["plan"]
    lines=[f"实验性计算 · {report['model']} · 31 自由度",
           "两点 Hertz 型支承、等效锥度、近似蠕滑与简化轮缘约束。",
           "工况与阈值为教学试验选项。实车与台架依据待接入。",
           f"实际求解 {report['solves']} 次 / 批准 {plan['maximum_solves']} 次",
           f"二系垂向行程约束 {plan['max_travel_mm']:g} mm",
           "证据 "+report["evidence_id"][:61],report["evidence_id"][61:]]
    result=report["comparison"]
    if result:
        lines.append("RK45 独立复算 · "+("候选通过本试验约束" if result["qualified"] else "候选未通过"))
        for row in result["candidate"]["rows"]:
            if "metrics" in row:
                m=row["metrics"]
                lines.append(f"{row['speed_m_s']:g} m/s · R {row['radius_m']:g} m · 行程 {m['secondary_travel_peak_mm']:.3f} mm"
                             f" · 最小轮重 {m['wheel_normal_min_kn']:.2f} kN · {row['evidence_id']}")
    else:
        lines.append("本次网格内无候选进入独立复算。")
    lines.append("完整输入、每工况指标及条件依赖见同编号 JSON。")
    for i,text in enumerate(lines):
        canvas.drawString(35,770-i*18,text)
    canvas.save()
    return stream.getvalue()
