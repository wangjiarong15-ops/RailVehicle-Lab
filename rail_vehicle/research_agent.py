"""Local engineering task graph with immutable approval and durable tool evidence."""

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
from threading import BoundedSemaphore
import zlib

import numpy as np

from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.engineering_trials import PARAMETERS, vary
from rail_vehicle.nonlinear_vehicle import (MODEL, WheelRailCase, case_config, performance, read_case, run_vehicle)
from rail_vehicle.twin_workflow import Research
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range

SCHEMA = "rail.engineering-agent.v1"
WORKER = BoundedSemaphore(1)
OBJECTIVES = {"lateral": "body_y_rms_m_s2", "vertical": "body_z_rms_m_s2"}


def digest(value):
    return sha256(json_text(value).encode()).hexdigest()


@dataclass(frozen=True)
class Plan:
    base: WheelRailCase
    axes: tuple = (("secondary_k", .75, 1.10), ("secondary_c", .75, 1.15))
    design: str = "factorial"
    draws: int = 4
    wheel_unloading: float = .40
    travel_mm: float = 35.
    budget: int = 9
    robustness: bool = True
    objectives: tuple = ("lateral", "vertical")
    observation: dict | None = None

    def __post_init__(self):
        if not isinstance(self.base, WheelRailCase) or not 1 <= len(self.axes) <= 3:
            raise ValueError("请选择基准工况及一至三个设计变量。")
        names = [name for name, _, _ in self.axes]
        if len(set(names)) != len(names):
            raise ValueError("设计变量不能重复。")
        for name, low, high in self.axes:
            if name not in PARAMETERS or not in_range(low, *PARAMETERS[name][2:]) or not in_range(high, *PARAMETERS[name][2:]) or low >= high:
                raise ValueError("参数区间超出轮轨试验范围。")
        if (self.design not in ("factorial", "lhs") or type(self.draws) is not int or not 2 <= self.draws <= 8
                or type(self.budget) is not int or not 5 <= self.budget <= 13 or type(self.robustness) is not bool
                or not in_range(self.wheel_unloading, .02, .95) or not in_range(self.travel_mm, 4., 100.)
                or self.objectives not in (("lateral",), ("vertical",), ("lateral", "vertical"))):
            raise ValueError("试验设计、目标、约束或计算预算无效。")
        for values in design_points(self):
            candidate(self.base, values)
        if reservation(self) > self.budget:
            raise ValueError(f"该计划最多需 {reservation(self)} 次求解。请增加预算或缩小试验设计。")
        if self.observation is not None and (not isinstance(self.observation, dict)
                or not {"dataset_sha256", "event_id", "index_1based", "source_label"} <= self.observation.keys()
                or len(json_text(self.observation)) > 6000):
            raise ValueError("观测事件摘要无效。")

    def payload(self):
        content = {**asdict(self), "base": case_config(self.base), "schema": SCHEMA, "model": MODEL}
        return {**content, "task_id": "research."+digest(content)}


def read_plan(payload):
    try:
        fields = {key: value for key, value in payload.items() if key not in ("schema", "model", "task_id")}
        fields["base"] = read_case(json_text(fields["base"]))
        fields["axes"] = tuple(tuple(axis) for axis in fields["axes"])
        fields["objectives"] = tuple(fields["objectives"])
        plan = Plan(**fields)
        if json_text(plan.payload()) == json_text(payload):
            return plan
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
        pass
    raise ValueError("计划版本或审批摘要不匹配。")


def design_points(plan):
    if plan.design == "factorial":
        values = product(*[(low, high) for _, low, high in plan.axes])
    else:
        random = np.random.default_rng(1841)
        columns = [low+(high-low)*(random.permutation(plan.draws)+.5)/plan.draws
                   for _, low, high in plan.axes]
        values = zip(*columns)
    return [dict(zip((axis[0] for axis in plan.axes), map(float, row))) for row in values]


def candidate(base, values):
    for name, value in values.items():
        base = vary(base, name, value)
    return base


def reservation(plan):
    return len(design_points(plan))+3+(2 if plan.robustness else 0)


@dataclass(frozen=True)
class Step:
    id: str
    tool: str
    depends: tuple = ()
    values: tuple = ()


def graph(plan):
    variants = [Step(f"case_{n}", "dynamic_response", ("observe",), tuple(point.items()))
                for n, point in enumerate(design_points(plan), 1)]
    return (Step("observe", "observation"), Step("knowledge", "engineering_knowledge", ("observe",)),
            Step("baseline", "dynamic_response", ("observe",)), *variants,
            Step("rank", "pareto", ("baseline", *(step.id for step in variants))),
            Step("verify_base", "independent_response", ("rank", "baseline")),
            Step("verify_choice", "independent_response", ("rank", "baseline")),
            *((Step("stress_base", "speed_probe", ("rank", "verify_choice", "verify_base")),
               Step("stress_choice", "speed_probe", ("rank", "verify_choice", "verify_base"))) if plan.robustness else ()),
            Step("report", "report", ("observe", "knowledge", "rank", "verify_base", "verify_choice",
                                      *(("stress_base", "stress_choice") if plan.robustness else ()))))


def _observation(plan, step, previous, solve):
    return {"source": "supplied_series" if plan.observation else "standalone_simulation",
            "event": plan.observation}


def _knowledge(plan, step, previous, solve):
    from rail_vehicle.copilot.knowledge import search

    return {"references":[{"id":p.id,"title":p.title,"source":p.source}
                          for p in search("横向振动 悬挂刚度阻尼 受迫振动")],
            "hypotheses":[{"name":name,"parameter":PARAMETERS[name][0],"test_range":[low,high],
                           "basis":"参数作用于模型中的对应力项，结果需要对照和独立复测。"}
                          for name,low,high in plan.axes]}


def constraints(plan, metrics):
    return [label for label, violated in (
        ("接触域", metrics["separated_samples"] or metrics["linear_limit_samples"]),
        ("轮重减载", metrics["wheel_unloading"] > plan.wheel_unloading),
        ("二系行程", metrics["secondary_travel_peak_mm"] > plan.travel_mm)) if violated]


def _solve(plan, values, mode, solve, speed=None):
    case = candidate(plan.base, values)
    if speed is not None:
        case = replace(case, speed_m_s=speed)
    row = {"parameters": values, "case": case_config(case), "accuracy": mode}
    try:
        response = solve(case, mode)
        metrics = performance(response)
        return {**row, "metrics": metrics, "violations": constraints(plan, metrics), "response": response}
    except (ValueError, FloatingPointError) as exc:
        return {**row, "violations": ["求解无效"], "reason": str(exc)}


def _dynamic(plan, step, previous, solve):
    values = dict(step.values)
    config = json_text(case_config(candidate(plan.base, values)))
    for key,item in previous.items():
        if item.get("accuracy")=="standard" and json_text(item["case"])==config and "response" in item:
            return {**item,"parameters":values,"cache_origin":key}
    return _solve(plan, values, "standard", solve)


def dominates(left, right, keys):
    a, b = ([row["metrics"][key] for key in keys] for row in (left, right))
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def _pareto(plan, step, previous, solve):
    rows = {key: {k: v for k, v in previous[key].items() if k != "response"} for key in step.depends}
    valid = {key: row for key, row in rows.items() if not row["violations"]}
    keys = [OBJECTIVES[key] for key in plan.objectives]
    front = [key for key,row in valid.items() if not any(dominates(other, row, keys) for other in valid.values())]
    baseline = json_text(rows["baseline"]["case"])
    ranked = sorted((key for key in front if json_text(valid[key]["case"])!=baseline),
                    key=lambda key: tuple(valid[key]["metrics"][name] for name in keys))
    choice = ranked[0] if ranked and "baseline" in valid else None
    interaction = None
    if plan.design=="factorial" and len(plan.axes)==2 and all("metrics" in rows[f"case_{n}"] for n in range(1,5)):
        a,b,c,d = [rows[f"case_{n}"]["metrics"][keys[0]] for n in range(1,5)]
        interaction = (d-c-b+a)/2
    return {"rows": rows, "pareto": front, "choice": choice, "interaction_effect": interaction}


def _independent(plan, step, previous, solve):
    rank = previous["rank"]
    if rank["choice"] is None:
        return {"skipped": "无可比较的可行候选"}
    values = rank["rows"][rank["choice"]]["parameters"] if step.id=="verify_choice" else {}
    return _solve(plan, values, "independent", solve)


def _stress(plan, step, previous, solve):
    choice = previous["rank"]["choice"]
    if choice is None or previous["verify_choice"].get("violations") or previous["verify_base"].get("violations"):
        return {"skipped": "无通过独立复算约束的候选"}
    values = previous["rank"]["rows"][choice]["parameters"] if step.id=="stress_choice" else {}
    speed = candidate(plan.base,values).speed_m_s
    return _solve(plan, values, "independent", solve, speed=min(55.,speed*1.1) if speed<=50. else speed*.9)


def _report(plan, step, previous, solve):
    rank = previous["rank"]
    outputs = {key: {k: v for k,v in previous[key].items() if k!="response"}
               for key in step.depends if key not in ("rank","observe","knowledge")}
    verified = outputs["verify_choice"]
    independent = outputs["verify_base"]
    key = OBJECTIVES[plan.objectives[0]]
    comparison = None
    if "metrics" in verified and "metrics" in independent:
        before, after = independent["metrics"][key], verified["metrics"][key]
        difference = abs(before-rank["rows"]["baseline"]["metrics"][key])+abs(after-rank["rows"][rank["choice"]]["metrics"][key])
        comparison = {"metric":key,"reduction":before-after,"solver_difference":difference,
                      "supported":not(verified["violations"] or independent["violations"]) and before-after>max(.0001,difference)}
    if "stress_choice" in outputs and "metrics" in outputs["stress_choice"] and "metrics" in outputs["stress_base"]:
        left, right = outputs["stress_base"], outputs["stress_choice"]
        probe = {"reduction": left["metrics"][key]-right["metrics"][key],
                 "within_declared_constraints": not(left["violations"] or right["violations"])}
    else:
        probe = None
    report = {"schema": SCHEMA, "model": MODEL, "plan": plan.payload(), "evidence": previous["observe"],
              "hypotheses":previous["knowledge"],
              "ranking": rank, "checks": outputs, "difference": comparison, "speed_probe": probe,
              "tools": [{"id":s.id,"tool":s.tool,"depends_on":s.depends,
                         "status":"cached" if previous.get(s.id,{}).get("cache_origin") else
                         "skipped" if previous.get(s.id,{}).get("skipped") else
                         "invalid" if previous.get(s.id,{}).get("reason") else "completed",
                         "response_id":previous.get(s.id,{}).get("response",{}).get("evidence_id")} for s in graph(plan)],
              "scope": "bounded model-based estimates and optional observation snapshot; field confirmation pending"}
    return {**report,"evidence_id":"review."+digest(report)}


# Closed local dispatch. Every tool is read-only with respect to the vehicle/data.
TOOLS = {"observation":_observation, "engineering_knowledge":_knowledge, "dynamic_response":_dynamic, "pareto":_pareto,
         "independent_response":_independent, "speed_probe":_stress, "report":_report}


def approved(plan, db_path=DB_PATH):
    payload = plan.payload()
    task = payload["task_id"]
    with get_connection(db_path) as connection:
        connection.execute("INSERT OR IGNORE INTO engineering_agent_tasks(task_id,approval_json) VALUES (?,?)",
                           (task,json_text(payload)))
    return task


def list_tasks(db_path=DB_PATH):
    if not Path(db_path).is_file():
        return []
    with get_connection(db_path) as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='engineering_agent_tasks'").fetchone() is None:
            return []
        return [dict(row) for row in connection.execute(
            "SELECT task_id,created_at FROM engineering_agent_tasks ORDER BY created_at DESC,rowid DESC LIMIT 20")]


def restore(task, db_path=DB_PATH):
    with get_connection(db_path) as connection:
        row = connection.execute("SELECT approval_json FROM engineering_agent_tasks WHERE task_id=?",(task,)).fetchone()
        rows = connection.execute("SELECT step_id,result,sha256 FROM engineering_agent_steps WHERE task_id=?",(task,)).fetchall()
    if row is None:
        raise ValueError("未找到审批记录。")
    plan = read_plan(json.loads(row["approval_json"]))
    if plan.payload()["task_id"] != task:
        raise ValueError("任务与批准计划不一致。")
    indexed = {row["step_id"]:row for row in rows}
    steps = {}
    for step in graph(plan):
        if step.id not in indexed:
            continue
        item = indexed.pop(step.id)
        raw = zlib.decompress(item["result"])
        if sha256(f"{task}:{step.id}:".encode()+raw).hexdigest()!=item["sha256"] or not all(dep in steps for dep in step.depends):
            raise ValueError("任务步骤摘要或依赖不匹配。")
        steps[step.id] = json.loads(raw)
    if indexed:
        raise ValueError("本版本不识别归档步骤。")
    return plan, steps


def execute(task, db_path=DB_PATH, *, checkpoint=lambda:None, progress=lambda _:None):
    plan, done = restore(task, db_path)

    def solve(case, mode):
        return run_vehicle(case, accuracy=mode, checkpoint=checkpoint)

    for step in graph(plan):
        checkpoint()
        if step.id not in done:
            result = TOOLS[step.tool](plan, step, done, solve)
            checkpoint()
            raw = json_text(result).encode()
            proof = sha256(f"{task}:{step.id}:".encode()+raw).hexdigest()
            with get_connection(db_path) as connection:
                connection.execute("INSERT INTO engineering_agent_steps VALUES (?,?,?,?)",
                                   (task,step.id,zlib.compress(raw),proof))
            done[step.id] = result
        progress(step.id)
    return done


class AgentJob(Research):
    active_budget_s = 220

    def __init__(self, request, db_path=DB_PATH):
        super().__init__(request)
        self.db_path = db_path
        self.results = {}

    def _run(self):
        if not WORKER.acquire(blocking=False):
            with self._condition:
                self.status,self.error = "error","本机已有工程研究任务，请稍后恢复。"
            return
        try:
            task = self.request.payload()["task_id"]
            def progress(step):
                with self._condition:
                    if step not in self.completed:
                        self.completed.append(step)
                    self.stage = len(self.completed)
            self.results = execute(task,self.db_path,checkpoint=self._checkpoint,progress=progress)
            with self._condition:
                self.report,self.status=self.results["report"],"complete"
        except InterruptedError:
            with self._condition:
                self.status="cancelled"
        except (ValueError, TimeoutError) as exc:
            with self._condition:
                self.status,self.error="error",str(exc)
        except Exception:
            with self._condition:
                self.status,self.error="error","任务执行或归档失败，已写入的步骤可从历史复核。"
        finally:
            WORKER.release()
