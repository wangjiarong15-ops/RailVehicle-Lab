"""Bounded virtual experiments with immutable evidence and explicit archival."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from io import BytesIO
from itertools import groupby, product
import json
from math import isfinite

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle

from rail_vehicle.analysis_report import _font_names
from rail_vehicle.coupled_vehicle import MODEL, case_config, read_case, simulate, summary
from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.vehicle_dynamics import axle_distances

SCHEMA = "rail.coupled-trial.v1"


def _within(value, low, high):
    return type(value) in (int, float) and isfinite(value) and low <= value <= high


def _axis(values, low, high):
    if (not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 3
            or not all(_within(v, low, high) for v in values) or len(set(values)) != len(values)):
        raise ValueError(f"扫描轴需要 1–3 个互异的有限值，范围 {low:g} 至 {high:g}。")
    return tuple(sorted(float(v) for v in values))


@dataclass(frozen=True)
class TrialPlan:
    speeds: tuple
    stiffness: tuple = (.7, 1., 1.3)
    damping: tuple = (.7, 1., 1.3)
    max_roll_mrad: float = 5.
    max_travel_mm: float = 20.

    def __post_init__(self):
        for key, low, high in (("speeds", 2, 70), ("stiffness", .5, 1.5), ("damping", .5, 1.5)):
            object.__setattr__(self, key, _axis(getattr(self, key), low, high))
        if not _within(self.max_roll_mrad, .01, 100) or not _within(self.max_travel_mm, .01, 200):
            raise ValueError("侧滚约束应为 0.01–100 mrad，二系横向行程约束应为 0.01–200 mm。")
        object.__setattr__(self, "max_roll_mrad", float(self.max_roll_mrad))
        object.__setattr__(self, "max_travel_mm", float(self.max_travel_mm))


def json_text(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _study_id(report):
    payload = {key: value for key, value in report.items() if key != "study_id"}
    return "trial."+sha256(json_text(payload).encode()).hexdigest()


def _evidence(result, plan, case):
    metrics = summary(result)
    reasons = []
    for key, exceeded in (
            ("roll", metrics["roll_peak_mrad"] > plan.max_roll_mrad),
            ("travel", metrics["secondary_y_peak_mm"] > plan.max_travel_mm),
            ("contact", metrics["contact_assumption_exceeded"])):
        if exceeded:
            reasons.append(key)
    if case.track == "level" or case.lateral_m == 0 or not any(row[0] for row in result["rail_y_m"]):
        reasons.append("no_lateral_input")
    else:
        required = case.profile[-1][0] if case.track == "custom" else case.length_m
        if case.track in ("sine", "random"):
            required *= 2  # ramp plus one complete spatial period
        if (axle_distances(case.vehicle)[-1]+required)/case.speed_m_s > case.duration_s:
            reasons.append("incomplete_excitation")
    return {"configuration_sha256": result["configuration_sha256"], "solver": result["solver"],
            "metrics": metrics, "violations": reasons}


def _sensitivities(rows):
    """Finite secants across each sampled axis, with other axes matched."""
    axes = ("speed_m_s", "stiffness_factor", "damping_factor")
    report = []
    complete = [row for row in rows if not {"no_lateral_input", "incomplete_excitation"}.intersection(row["violations"])]
    for axis in axes:
        def controls(row):
            return tuple(row[name] for name in axes if name != axis)

        slopes = []
        for _, group in groupby(sorted(complete, key=controls), key=controls):
            ordered = sorted(group, key=lambda row: row[axis])
            low, high = ordered[0], ordered[-1]
            if low[axis] != high[axis]:
                objective = "body_y_rms_m_s2"
                slopes.append((high["metrics"][objective]-low["metrics"][objective])/(high[axis]-low[axis]))
        if slopes:
            report.append({"axis":axis, "matched_pairs":len(slopes), "mean":sum(slopes)/len(slopes),
                           "min":min(slopes), "max":max(slopes)})
    return report


def run_trial(case, plan, *, checkpoint=lambda: None):
    """Return a deterministic archive and the refined playback data, if feasible."""
    transverse = case.transverse
    specs = list(product(plan.speeds, plan.stiffness, plan.damping))
    # Validate the entire grid before starting expensive calculations.
    candidates = [replace(case, speed_m_s=speed, transverse=replace(
        transverse, secondary_y_k_n_m=transverse.secondary_y_k_n_m*k,
        secondary_y_c_ns_m=transverse.secondary_y_c_ns_m*c)) for speed, k, c in specs]

    def solve(candidate, *, substeps=5):
        checkpoint()
        return simulate(candidate, substeps=substeps, include_modes=False)

    baseline = _evidence(solve(case), plan, case)
    rows = []
    for spec, candidate in zip(specs, candidates, strict=True):
        speed, k, c = spec
        evidence = baseline if candidate == case else _evidence(solve(candidate), plan, candidate)
        rows.append({"speed_m_s": speed, "stiffness_factor": k, "damping_factor": c,
                     "stiffness_n_m": candidate.transverse.secondary_y_k_n_m,
                     "damping_ns_m": candidate.transverse.secondary_y_c_ns_m, **evidence})
    ranking = sorted((i for i, row in enumerate(rows) if not row["violations"]),
                     key=lambda i: (rows[i]["metrics"]["body_y_rms_m_s2"], i))
    refine, playback = None, None
    refined_baseline = _evidence(solve(case, substeps=10), plan, case) if ranking else None
    checks = []
    for index in ranking:
        assessed = solve(candidates[index], substeps=10)
        evidence = _evidence(assessed, plan, candidates[index])
        coarse_rms = rows[index]["metrics"]["body_y_rms_m_s2"]
        fine_rms = evidence["metrics"]["body_y_rms_m_s2"]
        tolerance = max(1e-5, .02*abs(fine_rms))
        if abs(coarse_rms-fine_rms) > tolerance:
            evidence["violations"].append("step_convergence")
        checks.append({"index": index, **evidence})
        if not evidence["violations"] and fine_rms < (
                refine["metrics"]["body_y_rms_m_s2"] if refine else float("inf")):
            refine, playback = {**checks[-1], "case": case_config(candidates[index])}, assessed
    request = {"case": case_config(case), "plan": asdict(plan)}
    archive = {"schema": SCHEMA, "model": MODEL, "source": "teaching", "request": request,
               "objective": "body_y_rms_m_s2", "baseline": baseline, "refined_baseline": refined_baseline,
               "candidates": rows, "step_checks": checks, "sensitivity": _sensitivities(rows), "recommendation": refine}
    archive["study_id"] = _study_id(archive)
    return archive, playback


def rerun_trial(report, *, checkpoint=lambda: None):
    if report.get("schema") != SCHEMA or report.get("model") != MODEL or report.get("source") != "teaching":
        raise ValueError("历史试验使用了不支持的模型或版本。")
    request = report["request"]
    return run_trial(read_case(json_text(request["case"])), TrialPlan(**request["plan"]), checkpoint=checkpoint)


def save_trial(report, db_path=DB_PATH):
    if public_demo_enabled():
        raise ValueError("公开演示仅支持下载试验记录。")
    study_id = report["study_id"]
    if _study_id(report) != study_id or report.get("schema") != SCHEMA:
        raise ValueError("试验证据摘要不一致。")
    with get_connection(db_path) as connection:
        connection.execute("INSERT INTO coupled_trial_history (study_id, report_json) VALUES (?, ?) "
                           "ON CONFLICT(study_id) DO NOTHING", (study_id, json_text(report)))
    return study_id


def trial_history(db_path=DB_PATH):
    if public_demo_enabled():
        return []
    with get_connection(db_path) as connection:
        return [dict(row) for row in connection.execute("SELECT study_id, created_at FROM coupled_trial_history "
                                                        "ORDER BY created_at DESC, rowid DESC LIMIT 50")]


def load_trial(study_id, db_path=DB_PATH):
    if public_demo_enabled():
        return None
    with get_connection(db_path) as connection:
        row = connection.execute("SELECT report_json FROM coupled_trial_history WHERE study_id = ?", (study_id,)).fetchone()
    if row is None:
        return None
    report = json.loads(row["report_json"])
    if not isinstance(report, dict) or report.get("study_id") != study_id or _study_id(report) != study_id:
        raise ValueError("归档试验证据摘要不一致。")
    return report


def trial_pdf(report):
    """Export one compact page of calculated results and replay evidence."""
    buf = BytesIO()
    regular, bold = _font_names()
    page = Canvas(buf, pagesize=A4)
    page.setTitle("RailVehicle-Lab 整车虚拟试验")
    plan = report["request"]["plan"]
    case = report["request"]["case"]["case"]
    ink, heading = colors.HexColor("#21364a"), colors.HexColor("#153451")
    page.setFillColor(heading)
    page.setFont(bold, 17)
    page.drawString(38, 797, "RailVehicle-Lab 整车虚拟试验")
    page.setFont(regular, 9)
    page.setFillColor(ink)
    lines = ["教学参数与线性模型 · 尚无实车验证",
             f"{report['model']} / {SCHEMA}",
             f"试验 {report['study_id']}",
             f"输入 {case['track']} · 种子 {case['seed']} · 时长 {case['duration_s']} s",
             f"筛选要求为侧滚 ≤ {plan['max_roll_mrad']:g} mrad，二系横向行程 ≤ {plan['max_travel_mm']:g} mm。",
             "初筛 1 ms，复算 0.5 ms，输出 200 Hz。目标为全时窗车体横向加速度 RMS 最小。"]
    for i, line in enumerate(lines):
        page.drawString(38, 776-13*i, line)
    rows = [["序号", "速度 m/s", "刚度倍数", "阻尼倍数", "RMS m/s^2", "侧滚 mrad", "行程 mm", "初筛"]]
    for i, row in enumerate(report["candidates"], 1):
        metric = row["metrics"]
        rows.append([str(i), f"{row['speed_m_s']:g}", f"{row['stiffness_factor']:g}", f"{row['damping_factor']:g}",
                     f"{metric['body_y_rms_m_s2']:.5f}", f"{metric['roll_peak_mrad']:.3f}",
                     f"{metric['secondary_y_peak_mm']:.3f}", "否" if row["violations"] else "通过"])
    table = Table(rows, colWidths=[34,70,66,66,79,79,75,50], rowHeights=[17]+[15]*(len(rows)-1))
    table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), regular), ("FONTSIZE", (0,0), (-1,-1), 9),
        ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BACKGROUND", (0,0), (-1,0), heading), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("TEXTCOLOR", (0,1), (-1,-1), ink),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#edf3f8"), colors.white]),
        ("LINEBELOW", (0,-1), (-1,-1), .45, colors.HexColor("#a8bbcb")),
    ]))
    _, table_height = table.wrap(519, 540)
    y = 689-table_height
    table.drawOn(page, 38, y)
    page.setFont(regular, 9)
    page.setFillColor(ink)
    candidate = report["recommendation"]
    if candidate:
        baseline = report["refined_baseline"]["metrics"]["body_y_rms_m_s2"]
        lines = [f"网格最优可行工况 #{candidate['index']+1} · {candidate['metrics']['evidence_id']}",
                  f"基准与候选复算 RMS 为 {baseline:.6g} 和 {candidate['metrics']['body_y_rms_m_s2']:.6g} m/s^2。"
                  f"峰值时刻 {candidate['metrics']['peak_y_time_s']:.3f} s。",
                  f"复算配置 SHA-256 {candidate['configuration_sha256']}"]
    else:
        lines = ["当前网格无复算通过的方案。"]
    lines += ["完整配置及逐工况证据见同编号 JSON。",
              "轮对横向输入由轨面规定，线性接触只用于适用性筛查。"]
    for i, line in enumerate(lines, 1):
        page.drawString(38, y-15*i, line)
    page.save()
    return buf.getvalue()
