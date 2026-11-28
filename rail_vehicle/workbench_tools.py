"""Shared engineering tool protocol for the local and cloud research planners."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from time import monotonic

from jsonschema import Draft202012Validator

from rail_vehicle.copilot import tools as legacy
from rail_vehicle.copilot.context import Dataset
from rail_vehicle.engineering_trials import vary
from rail_vehicle.nonlinear_vehicle import WheelRailCase, performance, read_case, run_vehicle
from rail_vehicle.wheel_track import template


def serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str, separators=(",", ":"))


def digest(value):
    return sha256(serialize(value).encode()).hexdigest()


def snapshot(data):
    if data is None:
        return None
    fields = asdict(replace(data, replay_scope=None))
    return json.loads(serialize(fields))


def read_snapshot(fields):
    if fields is None:
        return None
    data = Dataset(**{key: value for key, value in fields.items() if key != "identity"})
    if data.identity != fields["identity"]:
        raise ValueError("当前批次快照与批准时的数据摘要不一致。")
    return data


def _legacy(name):
    def invoke(work, args, deps, checkpoint):
        output = None
        for update in legacy.execute(work, [{"tool": name, "args": args}]):
            checkpoint()
            output = update.get("result") or output
        return {"text": output.text, "references": list(output.refs), "rows": output.rows[:100],
                "figure": output.plot, "events": [{k: v for k, v in item.items() if k != "anchor"}
                                                  for item in output.events[:100]],
                "scene": output.scene, "citations": output.citations,
                "source": "knowledge" if name == "knowledge" else
                "schematic" if name in ("inspect", "current_inspection") else "observation_analysis"}
    return invoke


def _vehicle(work, args, deps, checkpoint):
    route = template(args["track"], args["radius_m"])
    case = WheelRailCase(route=route, speed_m_s=args["speed_m_s"], duration_s=args["duration_s"])
    case = vary(case, "secondary_k", args["secondary_k"])
    case = vary(case, "secondary_c", args["secondary_c"])
    return _response(case, args["accuracy"], checkpoint)


def _response(case, accuracy, checkpoint):
    response = run_vehicle(case, accuracy=accuracy, checkpoint=checkpoint)
    return {"text": "31 自由度教学模型已完成求解。", "source": "simulation", "response": response,
            "metrics": performance(response), "references": [response["evidence_id"]]}


def _active_case(work, args, deps, checkpoint):
    if not work.engineering or "simulation" not in work.engineering:
        raise ValueError("请先从动力学页面绑定具体工况。")
    return _response(read_case(serialize(work.engineering["simulation"])), args["accuracy"], checkpoint)


def _review_context(work, args, deps, checkpoint):
    if not work.engineering:
        raise ValueError("当前任务没有工程上下文。")
    return {"text": "已读取任务创建时冻结的项目、数据与工况配置。", "source": "engineering_context",
            "metrics": work.engineering, "references": ["context." + digest(work.engineering)]}


def _navigation(work, args, deps, checkpoint):
    return {"text": "已核对工作区入口。可在结果中确认打开。", "source": "engineering_context",
            "navigation": args["destination"], "references": []}


def _robust_vehicle(work, args, deps, checkpoint):
    from rail_vehicle.vehicle_uncertainty import paired_study

    context = work.engineering or {}
    case = read_case(serialize(context["simulation"])) if "simulation" in context else WheelRailCase(duration_s=1.25)
    output = paired_study(case, **args, checkpoint=checkpoint)
    return {"text": "已配对扰动 31 自由度模型并独立复算最不利样本。经验比例只描述声明的模拟样本。",
            "source": "simulation_comparison", "metrics": output,
            "references": [output["evidence_id"], *output["independent"]["references"]]}


def _verification(work, args, deps, checkpoint):
    runs = [item for item in deps.values() if item.get("source") == "simulation"]
    if len(runs) != 2 or any("response" not in item for item in runs):
        raise ValueError("独立复算对照须依赖两项求解。")
    first, second = (item["response"] for item in runs)
    if (first["case"] != second["case"] or first["model"] != second["model"]
            or first["solver"]["method"] == second["solver"]["method"]):
        raise ValueError("须固定模型和工况并改变积分器。")
    errors = {key: abs(runs[0]["metrics"][key] - runs[1]["metrics"][key])
              for key in ("body_y_rms_m_s2", "body_z_rms_m_s2", "secondary_travel_peak_mm", "wheel_unloading")}
    passed = (max(errors["body_y_rms_m_s2"], errors["body_z_rms_m_s2"]) <= args["rms_tolerance_m_s2"]
              and errors["secondary_travel_peak_mm"] <= args["travel_tolerance_mm"]
              and errors["wheel_unloading"] <= args["unloading_tolerance"])
    return {"text": "独立积分器对照通过所设数值容差。" if passed else "超出所设复算容差，需检查积分误差。",
            "source": "simulation_comparison", "metrics": {"passed": passed, **errors, "tolerances": args},
            "references": [first["evidence_id"], second["evidence_id"]]}


def _calibration(work, args, deps, checkpoint):
    from rail_vehicle.contact_calibration import fit_compliance, load_reference, compare_fixed_grid

    reference = load_reference()
    fit = fit_compliance(reference["manchester"], tangent_weight=args["tangent_weight"])
    return {"text": "已执行分组接触柔度校准与独立载荷对照。", "source": "published_numerical_reference",
            "metrics": fit, "load_check": compare_fixed_grid(reference["module3"]),
            "references": [fit["evidence_id"]]}


def _identify(work, args, deps, checkpoint):
    from rail_vehicle.twin_identification import identify
    from rail_vehicle.twin_workflow import demonstration, demo_plan

    case, (train, *_), held, (validation, *_) = demonstration()
    fit = identify(case, train, held, validation, demo_plan(case).bounds, spectral_weight=args["spectral_weight"],
                   checkpoint=checkpoint)
    return {"text": "已用合成记录拟合二系悬挂，并在独立合成记录上验证。", "source": "synthetic",
            "metrics": fit, "references": [fit["evidence_id"]]}


def _pareto(work, args, deps, checkpoint):
    from rail_vehicle.research_agent import dominates

    if len(deps) < 2 or any(entry["source"] != "simulation" for entry in deps.values()):
        raise ValueError("Pareto 至少依赖两项已完成的 31 自由度求解。")
    rows = {}
    for key, entry in deps.items():
        metrics = entry["metrics"]
        passed = (not metrics["separated_samples"] and not metrics["linear_limit_samples"]
                  and metrics["wheel_unloading"] <= args["unloading_ratio"]
                  and metrics["secondary_travel_peak_mm"] <= args["travel_mm"])
        rows[key] = {"metrics": metrics, "feasible": bool(passed)}
    valid = {key: row for key, row in rows.items() if row["feasible"]}
    axes = ("body_y_rms_m_s2", "body_z_rms_m_s2")
    front = [key for key, row in valid.items() if not any(dominates(other, row, axes) for other in valid.values())]
    return {"text": f"非支配解 {len(front)} 个。筛选仅针对本次模型与声明约束。", "source": "simulation_comparison",
            "rows": rows, "pareto": front, "references": [ref for entry in deps.values() for ref in entry["references"]]}


def _report(work, args, deps, checkpoint):
    if not deps:
        raise ValueError("报告须依赖已完成的证据步骤。")
    return {"text": "已整理依赖步骤的证据摘要。", "source": "evidence_summary",
            "rows": [{"step": key, "summary": item["text"], "source": item["source"]} for key, item in deps.items()],
            "references": [ref for entry in deps.values() for ref in entry.get("references", [])]}


@dataclass(frozen=True)
class Tool:
    title: str
    schema: dict
    units: str
    cost: int
    scope: str
    output: str
    handler: object


def schema(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_UNITS = {"statistics": "m/s²", "events": "UTC, m/s²", "review": "UTC, m/s²",
          "vibration": "Hz, m/s²", "compare": "km/h, m/s²", "quality": "UTC, s"}
TOOLS = {
    key: Tool(legacy.TOOLS[key].label, legacy.TOOLS[key].schema, _UNITS.get(key, "声明字段"), 1,
              "two_batches" if key == "compare" else "batch" if key not in legacy.UNBOUND_TOOLS else "local",
              "text, evidence references, optional plot/scene", _legacy(key))
    for key in ("vehicle", "quality", "statistics", "events", "review", "vibration", "compare", "knowledge", "inspect",
                "current_inspection", "sensors")
}
TOOLS.update({
    "open_workspace": Tool("核对目标工作区", schema(destination=legacy.enum((
        "analysis", "bogie", "simulation", "twin", "validation", "report"))), "工作区",
        1, "local", "approved navigation suggestion", _navigation),
    "project_context": Tool("核对工程任务上下文", schema(), "继承原证据单位", 1, "local",
                            "frozen project and analysis context", _review_context),
    "active_solve": Tool("执行已绑定的 31 自由度工况", schema(accuracy=legacy.enum(("standard", "independent"))),
                         "m, m/s, s", 3, "local", "response for the frozen case", _active_case),
    "verify_solve": Tool("核对同工况独立积分器", schema(rms_tolerance_m_s2=legacy.number(1e-8, .05),
                         travel_tolerance_mm=legacy.number(1e-6, 2), unloading_tolerance=legacy.number(1e-7, .05)),
                         "m/s², mm, 比例", 1, "local", "independent numerical verification", _verification),
    "robust31": Tool("31 自由度随机配对试验",
                     schema(draws={"type": "integer", "minimum": 4, "maximum": 8},
                            seed={"type": "integer", "minimum": 0, "maximum": 2**32 - 1},
                            spread=legacy.number(.01, .15), unloading_ratio=legacy.number(.02, .95),
                            travel_mm=legacy.number(4, 100)),
                     "m/s², mm, 比例", 12, "local", "seeded response quantiles and independent check", _robust_vehicle),
    "solve31": Tool("求解 31 自由度车辆动力学", schema(track=legacy.enum(("straight", "curve", "reverse")),
                   radius_m=legacy.number(125, 2000), speed_m_s=legacy.number(5, 55),
                   duration_s={"enum": [1., 1.25, 1.5, 2., 3.]},
                   secondary_k=legacy.number(.6, 1.3), secondary_c=legacy.number(.6, 1.3),
                   accuracy=legacy.enum(("standard", "independent"))), "m, m/s, s, 比例", 3,
                   "local", "performance metrics and numerical time history", _vehicle),
    "contact_fit": Tool("对照 CONTACT 并校准有效柔度", schema(tangent_weight=legacy.number(.1, 10)),
                        "GN/m^1.5, N, mm", 2, "local", "grouped fit and held-out errors", _calibration),
    "identify_demo": Tool("悬挂时频辨识的独立合成验算", schema(spectral_weight=legacy.number(0, 2)), "SI",
                          3, "local", "identification and independent validation", _identify),
    "pareto31": Tool("比较已有求解并筛选 Pareto 解", schema(unloading_ratio=legacy.number(.02, .95),
                     travel_mm=legacy.number(4, 100)), "减载率, mm, m/s²", 1, "local", "feasible and nondominated sets", _pareto),
    "evidence_report": Tool("汇总已完成步骤的工程证据", schema(), "继承证据单位", 1, "local",
                            "evidence summary", _report),
})


def definitions():
    return {key: {"description": value.title, "parameters": value.schema, "units": value.units,
                  "cost": value.cost, "context": value.scope, "permission": "read_only", "output": value.output}
            for key, value in TOOLS.items()}


def invoke(tool, args, work, dependencies, checkpoint):
    spec = TOOLS[tool]
    if not Draft202012Validator(spec.schema).is_valid(args):
        raise ValueError("工具参数不符合协议。")
    if (spec.scope != "local" and not work.data) or (spec.scope == "two_batches" and not work.other):
        raise ValueError("未绑定工具所需批次。")
    start = monotonic()
    checkpoint()
    output = json.loads(serialize(spec.handler(work, args, dependencies, checkpoint)))
    checkpoint()
    if not isinstance(output.get("text"), str) or "source" not in output:
        raise ValueError("工具输出不符合协议。")
    return {"output": output, "proof": digest(output), "elapsed_ms": round((monotonic()-start)*1000)}
