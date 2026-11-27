"""User-approved, local engineering tasks and versioned evidence archives."""

from dataclasses import asdict, dataclass, replace
from io import BytesIO
import json
from threading import BoundedSemaphore, Condition, Thread
from time import monotonic

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from rail_vehicle.analysis_report import _font_names
from rail_vehicle.coupled_vehicle import MODEL, CoupledCase, case_config, read_case, simulate
from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.twin_data import check_case, compare, digest, quality, synthetic_observations
from rail_vehicle.twin_identification import coefficients, identify, parameter_bounds
from rail_vehicle.twin_optimization import OBJECTIVES, OptimizationPlan, optimize
from rail_vehicle.virtual_trials import json_text

SCHEMA = "rail.twin-research.v1"
STAGES = ("观测和时基复核", "有界参数辨识", "多工况优化与半步长复算", "整理计算证据与报告")
DEMO_RECIPE = "rail.twin-demo.deterministic.v1"
_WORKERS = BoundedSemaphore(2)


def demonstration():
    base = CoupledCase(track="sine",duration_s=3,speed_m_s=20.)
    held = replace(base,track="random",seed=97,speed_m_s=24.)
    truth = {"secondary_y_k_n_m":142000., "secondary_y_c_ns_m":21000., "antiroll_k_nm_rad":250000.}
    train = synthetic_observations(coefficients(base,truth),rate=65,seed=10,noise=.02,clock_offset=.015,clock_scale=1.001)
    validation = synthetic_observations(coefficients(held,truth),rate=51,seed=11,noise=.03,clock_offset=-.018)
    return base, train, held, validation


def demo_plan(case):
    return OptimizationPlan({"secondary_y_k_n_m":(80000.,280000.),"secondary_y_c_ns_m":(8000.,32000.),
                             "antiroll_k_nm_rad":(0.,450000.)},(case.speed_m_s,),("bump","sine"),
                            budget=28)


@dataclass(frozen=True)
class Request:
    case: CoupledCase
    observations: object
    validation_case: CoupledCase
    validation: object
    plan: OptimizationPlan
    identification_budget: int = 84
    recipe: str | None = None

    def __post_init__(self):
        parameter_bounds(self.plan.bounds)
        if type(self.identification_budget) is not int or not 24 <= self.identification_budget <= 180:
            raise ValueError("辨识预算须为 24 至 180 次模型计算。")
        if self.recipe is not None and (self.recipe != DEMO_RECIPE or self.observations.source != "demonstration"):
            raise ValueError("虚构任务版本无效。")


class Research:
    """One approved run with stage-level resumption and cooperative solver checkpoints."""

    def __init__(self, request):
        self.request = request
        self._condition = Condition()
        self.status = "draft"
        self.stage = 0
        self.completed = []
        self.error = None
        self.report = None

    def state(self):
        with self._condition:
            return self.status, self.stage, self.error, self.report

    def start(self):
        with self._condition:
            if self.status == "paused":
                self.status = "running"
                self._condition.notify_all()
            elif self.status in ("draft","error"):
                self.status, self.error = "running", None
                self._deadline = monotonic()+150
                Thread(target=self._run,name="rail-twin",daemon=True).start()

    def pause(self):
        with self._condition:
            if self.status == "running":
                self.status = "paused"

    def cancel(self):
        with self._condition:
            if self.status in ("running","paused"):
                self.status = "cancelling"
                self._condition.notify_all()

    def _checkpoint(self):
        with self._condition:
            if self.status == "paused":
                started = monotonic()
                if not self._condition.wait_for(lambda:self.status!="paused",timeout=300):
                    raise TimeoutError("本轮暂停时间已到，可继续已完成的阶段。")
                self._deadline += monotonic()-started
            if self.status == "cancelling":
                raise InterruptedError
            if monotonic() >= self._deadline:
                raise TimeoutError("已用完本轮计算时间预算。可保留完成阶段并重试。")

    def _run(self):
        r = self.request
        if not _WORKERS.acquire(blocking=False):
            with self._condition:
                self.status,self.error = "error","本机试验任务繁忙，可重试当前阶段。"
            return
        try:
            if self.stage == 0:
                self._checkpoint()
                check_case(r.observations,r.case)
                check_case(r.validation,r.validation_case)
                self._checkpoint()
                comparison = compare(r.observations,simulate(r.case,include_modes=False))
                self.completed.append({key:value for key,value in comparison.items() if key!="channels"})
                self.stage = 1
            if self.stage == 1:
                calibration = identify(r.case,r.observations,r.validation_case,r.validation,r.plan.bounds,
                                       budget=r.identification_budget,checkpoint=self._checkpoint)
                self.completed.append(calibration)
                self.stage = 2
            if self.stage == 2:
                calibration = self.completed[1]
                changed = calibration["identified"]
                if changed:
                    optimization = optimize(coefficients(r.case,changed),r.plan,checkpoint=self._checkpoint)
                else:
                    optimization = None
                self.completed.append(optimization)
                self.stage = 3
            self._checkpoint()
            with self._condition:
                report = {"schema":SCHEMA,"model":MODEL,"source":r.observations.source,
                          "recipe":r.recipe,"case":case_config(r.case),"plan":asdict(r.plan),"quality_and_model":self.completed[0],
                          "identification":self.completed[1],"optimization":self.completed[2],
                          "approved_budget":{"validation_baseline":1,"identification":r.identification_budget,"optimization":r.plan.budget,
                                             "worker_active_s":150}}
                report["evidence_id"] = "research."+digest(report)
                self.report = report
                self.status, self.stage = "complete",len(STAGES)
        except InterruptedError:
            with self._condition:
                self.status = "cancelled"
        except (ValueError,TimeoutError) as exc:
            with self._condition:
                self.status, self.error = "error", str(exc)
        except Exception:
            with self._condition:
                self.status, self.error = "error","本机计算中止，可重试当前阶段。"
        finally:
            _WORKERS.release()


def _verified(archive):
    if not isinstance(archive,dict) or archive.get("schema") != SCHEMA or archive.get("model") != MODEL:
        raise ValueError("工程任务归档版本无效。")
    expected = "research."+digest({key:v for key,v in archive.items() if key!="evidence_id"})
    if archive.get("evidence_id") != expected:
        raise ValueError("工程任务摘要与归档内容不匹配。")
    return archive


def save_research(report, db_path=DB_PATH):
    if public_demo_enabled():
        raise ValueError("公开演示仅提供报告下载。")
    _verified(report)
    with get_connection(db_path) as db:
        db.execute("INSERT INTO twin_research_history(evidence_id,report_json) VALUES(?,?) "
                   "ON CONFLICT(evidence_id) DO NOTHING",(report["evidence_id"],json_text(report)))


def history(db_path=DB_PATH):
    if public_demo_enabled():
        return []
    with get_connection(db_path) as db:
        return [(r["evidence_id"],r["created_at"]) for r in db.execute(
            "SELECT evidence_id,created_at FROM twin_research_history ORDER BY created_at DESC,rowid DESC LIMIT 30")]


def read_research(evidence_id, db_path=DB_PATH):
    if public_demo_enabled():
        return None
    with get_connection(db_path) as db:
        row = db.execute("SELECT report_json FROM twin_research_history WHERE evidence_id=?",(evidence_id,)).fetchone()
    return _verified(json.loads(row["report_json"])) if row else None


def rerun_request(archive, request=None):
    archive = _verified(archive)
    identification = archive["identification"]
    if archive["recipe"] == DEMO_RECIPE:
        case, (train,*_), held, (validation,*_) = demonstration()
    elif request:
        case, train, held, validation = request.case, request.observations, request.validation_case, request.validation
    else:
        raise ValueError("请重新绑定原始辨识与验证文件后再复算。")
    if (train.evidence() != identification["training"] or validation.evidence() != identification["validation"]
            or read_case(json_text(identification["case"])) != case or read_case(json_text(identification["validation_case"])) != held):
        raise ValueError("复算记录、元数据或模型工况与归档不一致。")
    return Request(case,train,held,validation,OptimizationPlan(**archive["plan"]),identification["budget"],archive["recipe"])


def research_pdf(report):
    _verified(report)
    output = BytesIO()
    regular,bold = _font_names()
    page = Canvas(output,pagesize=A4)
    page.setTitle("RailVehicle-Lab 车辆数字孪生工程记录")
    page.setFillColor(colors.HexColor("#173451"))
    page.setFont(bold,17)
    page.drawString(36,796,"RailVehicle-Lab 车辆数字孪生工程记录")
    fit = report["identification"]
    opt = report["optimization"]
    lines = [f"来源 {report['source']} · 线性教学模型 {report['model']}",
             "实车与设备独立验收待完成。完整参数、逐通道指标与工况在同编号 JSON。",
             f"工程证据 {report['evidence_id']}",
             f"观测 CSV SHA-256 {fit['training']['csv_sha256']}",
             f"验证 CSV SHA-256 {fit['validation']['csv_sha256']}",
             f"辨识证据 {fit['evidence_id']} · {fit['evaluations']} 次求解",
             f"独立验证归一化 RMSE {fit['baseline_normalized_rmse']:.6g} → {fit['validation_normalized_rmse']:.6g}"]
    for key,value in (fit["identified"] or {}).items():
        lines.append(f"{key}  {value:.6g} SI")
    if opt:
        lines += [f"优化证据 {opt['evidence_id']} · {opt['evaluations']} 次求解",
                  f"半步长验证 Pareto 候选 {len(opt['pareto'])} 个"]
        if choice:=opt["first_choice"]:
            for key in opt["plan"]["objectives"]:
                lines.append(f"{OBJECTIVES[key]} · 最不利基准及候选 {opt['baseline']['worst'][key]:.6g} → {choice['worst'][key]:.6g}")
    if fit["flags"]:
        lines.append("辨识条件不足。优化阶段未执行。")
    page.setFont(regular,9)
    for i,line in enumerate(lines):
        page.drawString(36,765-i*19,line)
    page.save()
    return output.getvalue()
