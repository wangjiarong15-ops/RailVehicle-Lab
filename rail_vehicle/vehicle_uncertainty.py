"""Paired, seeded model sensitivity over a bounded 31-DOF case ensemble."""

from dataclasses import replace

import numpy as np

from rail_vehicle.engineering_trials import vary
from rail_vehicle.nonlinear_vehicle import case_config, performance, run_vehicle
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range
from hashlib import sha256


def paired_study(case, *, draws, seed, spread, unloading_ratio, travel_mm, checkpoint=lambda: None):
    if (type(draws) is not int or not 4 <= draws <= 8 or type(seed) is not int or not 0 <= seed < 2**32
            or not in_range(spread, .01, .15) or not in_range(unloading_ratio, .02, .95)
            or not in_range(travel_mm, 4, 100) or case.duration_s > 3):
        raise ValueError("不确定性试验预算、扰动或约束无效。试验工况最长 3 s。")
    perturbations = np.random.default_rng(seed).uniform(1 - spread, 1 + spread, (draws, 2)).tolist()
    rows = []
    paired_cases = []
    for index, (speed, damping) in enumerate(perturbations):
        checkpoint()
        original = replace(case, speed_m_s=case.speed_m_s * speed)
        reference = vary(original, "secondary_c", original.secondary_lateral_c_scale * damping)
        candidate = vary(reference, "secondary_k", reference.secondary_lateral_k_scale * .85)
        paired_cases.append((reference, candidate))
        observed = {}
        for name, current in (("baseline", reference), ("candidate", candidate)):
            solved = run_vehicle(current, checkpoint=checkpoint)
            response = performance(solved)
            observed[name] = {"lateral_rms_m_s2": response["body_y_rms_m_s2"],
                              "secondary_travel_mm": response["secondary_travel_peak_mm"],
                              "unloading_ratio": response["wheel_unloading"],
                              "evidence": solved["evidence_id"],
                              "feasible": bool(response["wheel_unloading"] <= unloading_ratio
                                               and response["secondary_travel_peak_mm"] <= travel_mm
                                               and not response["separated_samples"]
                                               and not response["linear_limit_samples"])}
        reduction = observed["baseline"]["lateral_rms_m_s2"] - observed["candidate"]["lateral_rms_m_s2"]
        rows.append({"index": index, "speed_multiplier": speed, "secondary_damping_multiplier": damping,
                     "reduction_m_s2": reduction, **observed})
    worst = min(rows, key=lambda row: row["reduction_m_s2"])
    check = [run_vehicle(current, accuracy="independent", checkpoint=checkpoint)
             for current in paired_cases[worst["index"]]]
    independent_reduction = performance(check[0])["body_y_rms_m_s2"] - performance(check[1])["body_y_rms_m_s2"]
    inputs = np.array(perturbations)
    reductions = np.array([row["reduction_m_s2"] for row in rows])
    correlations = {}
    for column, label in enumerate(("speed", "secondary_lateral_damping")):
        correlations[label] = float(np.corrcoef(inputs[:, column], reductions)[0, 1]) if np.std(reductions) > 1e-12 else None
    result = {"schema": "rail.vehicle-uncertainty.v1", "source": "synthetic_model_parameter_sweep",
              "baseline": case_config(case), "draws": draws, "seed": seed,
              "distribution": {"family": "independent_uniform_factor", "half_width": spread},
              "candidate": {"secondary_lateral_stiffness_multiplier": .85},
              "constraints": {"unloading_ratio": unloading_ratio, "secondary_travel_mm": travel_mm},
              "sample_candidate_feasible_fraction": sum(row["candidate"]["feasible"] for row in rows) / draws,
              "empirical_reduction_m_s2": np.quantile(reductions, [.05, .5, .95]).tolist(),
              "sensitivity_pearson": correlations, "rows": rows,
              "independent": {"worst_reduction_index": worst["index"], "method": "RK45",
                              "reduction_m_s2": independent_reduction,
                              "absolute_difference_m_s2": abs(independent_reduction - worst["reduction_m_s2"]),
                              "references": [item["evidence_id"] for item in check]}}
    return {**result, "evidence_id": "vehicle-uncertainty." + sha256(json_text(result).encode()).hexdigest()}
