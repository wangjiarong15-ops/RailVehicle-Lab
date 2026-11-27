"""Budgeted multi-condition search, constraint checks and half-step verification."""

from dataclasses import asdict, dataclass, replace
from itertools import product
from math import isfinite

import numpy as np

from rail_vehicle.coupled_vehicle import case_config, simulate, summary
from rail_vehicle.twin_data import check_excitation_coverage, digest
from rail_vehicle.twin_identification import coefficients, parameter_bounds

OBJECTIVES = {"body_y_rms_m_s2":"横向加速度 RMS", "body_z_rms_m_s2":"垂向加速度 RMS",
              "roll_peak_mrad":"侧滚峰值", "secondary_y_peak_mm":"二系横向行程"}
STRATEGIES = {"grid":"规则网格", "global":"空间填充全域搜索", "local":"局部坐标搜索", "hybrid":"全域与局部组合"}
FLOOR = dict(zip(OBJECTIVES, (.001,.001,.01,.05), strict=True))


def _bounded(value, lo, hi):
    return type(value) in (int,float) and isfinite(value) and lo <= value <= hi


@dataclass(frozen=True)
class OptimizationPlan:
    bounds: dict
    speeds: tuple
    tracks: tuple
    objectives: tuple = ("body_y_rms_m_s2", "roll_peak_mrad")
    weights: tuple = (1.,1.)
    max_roll_mrad: float = 5.
    max_travel_mm: float = 20.
    budget: int = 64
    strategy: str = "hybrid"
    seed: int = 42

    def __post_init__(self):
        object.__setattr__(self,"bounds",parameter_bounds(self.bounds))
        if (not isinstance(self.speeds,(tuple,list)) or not 1 <= len(self.speeds) <= 3
                or not all(_bounded(v,2,70) for v in self.speeds) or len(set(self.speeds)) != len(self.speeds)):
            raise ValueError("请填写 1 至 3 个不同速度，范围为 2 至 70 m/s。")
        if (not isinstance(self.tracks,(tuple,list)) or not 1 <= len(self.tracks) <= 3
                or not all(isinstance(v,str) and v in ("sine","random","bump","custom") for v in self.tracks)
                or len(set(self.tracks)) != len(self.tracks) or len(self.tracks)*len(self.speeds) > 6):
            raise ValueError("激励和速度组合须在 1 至 6 个工况内。")
        if (not isinstance(self.objectives,(tuple,list)) or not 1 <= len(self.objectives) <= len(OBJECTIVES)
                or not all(isinstance(key,str) and key in OBJECTIVES for key in self.objectives)
                or len(set(self.objectives)) != len(self.objectives) or not isinstance(self.weights,(tuple,list))
                or len(self.weights) != len(self.objectives) or not all(_bounded(v,.01,100) for v in self.weights)):
            raise ValueError("请选择不同的目标，并为每个目标设置正权重。")
        if (not isinstance(self.strategy,str) or self.strategy not in STRATEGIES
                or type(self.budget) is not int or not 12 <= self.budget <= 160 or type(self.seed) is not int
                or not 0 <= self.seed < 2**32 or not _bounded(self.max_roll_mrad,.01,100)
                or not _bounded(self.max_travel_mm,.01,200)):
            raise ValueError("优化策略、预算、随机种子或约束无效。")
        if self.budget < 5*len(self.speeds)*len(self.tracks):
            raise ValueError("预算至少应覆盖每个工况的五次求解。")
        for name in ("speeds","tracks","objectives","weights"):
            object.__setattr__(self,name,tuple(getattr(self,name)))


def _front(records, objectives):
    def dominates(first, second):
        a,b = ([r["worst"][key] for key in objectives] for r in (first,second))
        return all(x <= y for x,y in zip(a,b,strict=True)) and any(x < y for x,y in zip(a,b,strict=True))
    return [r for r in records if not any(dominates(other,r) for other in records if other is not r)]


def optimize(case, plan, *, checkpoint=lambda: None):
    if not case.lateral_m and any(o != "body_z_rms_m_s2" for o in plan.objectives):
        raise ValueError("横向与侧滚目标需要非零横向激励。")
    if plan.objectives == ("body_z_rms_m_s2",):
        raise ValueError("现有设计变量只作用于横向子系统，垂向 RMS 可作为联合目标。")
    if "custom" in plan.tracks and not case.profile:
        raise ValueError("自定义激励须随工况提供轨迹。")
    cases = [replace(case,speed_m_s=v,track=track,profile=case.profile if track=="custom" else ())
             for v,track in product(plan.speeds,plan.tracks)]
    for scenario in cases:
        check_excitation_coverage(scenario)
    keys = tuple(plan.bounds)
    lo,hi = np.asarray(list(plan.bounds.values())).T
    baseline = {k:getattr(case.transverse,k) for k in keys}
    original = np.clip((np.array(list(baseline.values()))-lo)/(hi-lo),0,1)
    weights = np.array(plan.weights)/sum(plan.weights)
    total = 0

    def evaluate(values, step):
        nonlocal total
        results = []
        violations = []
        for scenario in cases:
            checkpoint()
            if total >= plan.budget:
                raise RuntimeError("优化求解计数超过已批准预算。")
            result = simulate(coefficients(scenario,values),substeps=step,include_modes=False)
            total+=1
            metric = summary(result)
            rejected = []
            if metric["roll_peak_mrad"] > plan.max_roll_mrad:
                rejected.append("roll")
            if metric["secondary_y_peak_mm"] > plan.max_travel_mm:
                rejected.append("travel")
            if metric["contact_assumption_exceeded"]:
                rejected.append("contact")
            violations.extend(rejected)
            results.append({"speed_m_s":scenario.speed_m_s,"track":scenario.track,"metrics":metric,"violations":rejected,
                            "sha256":result["configuration_sha256"]})
        return {"values":values, "substeps":step, "conditions":results, "violations":sorted(set(violations)),
                "worst":{key:max(r["metrics"][key] for r in results) for key in OBJECTIVES}}

    count = plan.budget//len(cases)
    refined_slots = min(6,max(1,count//4))
    reference = evaluate(baseline,5)
    normalization = {o:max(reference["worst"][o],FLOOR[o]) for o in plan.objectives}

    def score(row):
        return float(weights@np.array([row["worst"][o]/normalization[o] for o in plan.objectives]))

    explored, seen = [], set()
    limit = count-refined_slots-2  # reserve a fine baseline and independent candidate evaluations

    def candidate(x):
        x = np.round(np.clip(x,0,1),9)
        token = tuple(x)
        if token in seen or len(explored) >= limit:
            return
        seen.add(token)
        values = dict(zip(keys,map(float,lo+x*(hi-lo)),strict=True))
        row = evaluate(values,5)
        explored.append({**row,"norm":x.tolist(),"score":score(row)})

    candidate(original)
    if plan.strategy != "local":
        for i in range(len(keys)):
            for edge in (0.,1.):
                trial = original.copy()
                trial[i] = edge
                candidate(trial)
    if plan.strategy == "grid":
        density = max(2,int(round((limit+1)**(1/len(keys)))))
        for x in product(np.linspace(0,1,density),repeat=len(keys)):
            candidate(x)
            if len(explored) >= limit:
                break
    if plan.strategy in ("global","hybrid"):
        rng = np.random.default_rng(plan.seed)
        expected = max(0,(limit if plan.strategy=="global" else max(2,limit*2//3))-len(explored))
        if expected:
            design = np.column_stack([(rng.permutation(expected)+rng.random(expected))/expected for _ in keys])
            for x in design:
                candidate(x)
    if plan.strategy in ("local","hybrid"):
        for step in (.25,.125,.0625,.03125,.015625):
            if len(explored) >= limit:
                break
            center = min(explored,key=lambda r:(bool(r["violations"]),r["score"]))["norm"]
            for i in range(len(keys)):
                for sign in (-1,1):
                    point = center.copy()
                    point[i] += sign*step
                    candidate(point)
    valid = [r for r in explored if not r["violations"]]
    pareto = sorted(_front(valid,plan.objectives),key=score)
    pool = (pareto+sorted((r for r in valid if r not in pareto),key=score))[:refined_slots]
    fine_reference = evaluate(baseline,10)
    refined = []
    for row in pool:
        fine = evaluate(row["values"],10)
        if any(abs(fine["worst"][o]-row["worst"][o]) > max(FLOOR[o]*.02,.025*fine["worst"][o]) for o in plan.objectives):
            fine["violations"].append("time_step")
        refined.append({**fine,"score":score(fine)})
    front = sorted(_front([r for r in refined if not r["violations"]],plan.objectives),key=score)
    ranking = []
    for i,key in enumerate(keys):
        matched = sorted((row for row in explored if all(abs(n-original[j])<=1e-8 for j,n in enumerate(row["norm"]) if i!=j)),
                         key=lambda r:r["norm"][i])
        if len(matched)>1:
            a,b = matched[0],matched[-1]
            ranking.append({"variable":key,"score_span":(b["score"]-a["score"])/(b["norm"][i]-a["norm"][i])})
    ranking.sort(key=lambda row:abs(row["score_span"]), reverse=True)
    report = {"schema":"rail.twin-optimization.v1","source":"simulation","case":case_config(case),
              "plan":asdict(plan),"normalization":normalization,"evaluations":total,"coarse":explored,
              "refined":refined,"baseline":fine_reference,"pareto":front,"sensitivity":ranking,
              "first_choice":front[0] if front else None}
    report["evidence_id"] = "optimization."+digest(report)[:20]
    return report


def replay_cases(case, report, *, condition=0, candidate=0):
    plan = report["plan"]
    speed, track = tuple(product(plan["speeds"],plan["tracks"]))[condition]
    base = replace(case, speed_m_s=speed, track=track, profile=case.profile if track=="custom" else ())
    changed = coefficients(base,report["pareto"][candidate]["values"])
    return base,changed
