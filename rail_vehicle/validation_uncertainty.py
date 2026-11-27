"""Paired response intervals and feasibility under declared synthetic parameter variation."""

from dataclasses import asdict, replace
from hashlib import sha256

import numpy as np

from rail_vehicle.flexible_track import TrackCase, response
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range


def metrics(result):
    q=np.asarray(result["normal_n"])
    static=result["parameters"]["static_normal_n"]
    return float(np.sqrt(np.mean((q-static)**2))), float(np.min(q)/static)


def study(case: TrackCase, *, draws=12, seed=1924, load_min_ratio=.75, spread=.12):
    if (type(draws) is not int or not 6<=draws<=24 or type(seed) is not int or not 0<=seed<2**32
            or not in_range(spread,.01,.25) or not in_range(load_min_ratio,.15,.98)):
        raise ValueError("抽样次数、比例或约束无效。")
    rng=np.random.default_rng(seed)
    table=[]
    prng=rng.uniform(-1,1,(draws,5)).tolist()
    for k,depth,period,load,damp in prng:
        variations={"pad_stiffness_n_m":case.pad_stiffness_n_m*(1+spread*k),
                    "defect_depth_m":case.defect_depth_m*(1+spread*depth),
                    "crossing_time_s":case.crossing_time_s*(1+spread*period),
                    "static_normal_n":case.static_normal_n*(1+spread*load),
                    "pad_damping_ns_m":case.pad_damping_ns_m*(1+spread*(.6*k+.4*damp))}
        reference=replace(case,**variations)
        candidate=replace(reference,pad_damping_ns_m=reference.pad_damping_ns_m*1.25)
        base_rms,base_min=metrics(response(reference))
        candidate_rms,candidate_min=metrics(response(candidate))
        table.append({"parameters":variations,"baseline_rms_n":base_rms,"candidate_rms_n":candidate_rms,
                      "baseline_min_ratio":base_min,"candidate_min_ratio":candidate_min,"reduction_n":base_rms-candidate_rms})
    reductions=np.array([row["reduction_n"] for row in table])
    input_names=tuple(table[0]["parameters"])
    factors=np.array([[row["parameters"][key] for key in input_names] for row in table])
    correlations={}
    for idx,key in enumerate(input_names):
        correlations[key]=float(np.corrcoef(factors[:,idx],reductions)[0,1]) if (
                            np.std(factors[:,idx])>1e-9 and np.std(reductions)>1e-9) else None
    worst=min(range(draws),key=lambda i:table[i]["candidate_min_ratio"])
    reference=replace(case,**table[worst]["parameters"])
    candidate=replace(reference,pad_damping_ns_m=reference.pad_damping_ns_m*1.25)
    independent=metrics(response(reference,"Radau"))[0]-metrics(response(candidate,"Radau"))[0]
    numerical_error=abs(table[worst]["reduction_n"]-independent)
    conservative, median, high=map(float,np.quantile(reductions,[.05,.5,.95]))
    fail=sum(row["candidate_min_ratio"]<load_min_ratio for row in table)
    result={"schema":"rail.uncertainty.v1","case":asdict(case),"source":"synthetic_parameter_sweep",
            "draws":draws,"seed":seed,"spread":spread,"candidate":"pad_damping_plus_25_percent",
            "load_ratio_limit":load_min_ratio,"empirical_reduction_n":[conservative,median,high],
            "candidate_failure_rate":fail/draws,"input_correlations":correlations,"rows":table,
            "independent":{"solver":"Radau","row":worst,"paired_reduction_n":independent,
                           "difference_n":numerical_error},
            "supported_on_draws":bool(not fail and conservative>2*numerical_error)}
    return {**result,"evidence_id":"uncertainty."+sha256(json_text(result).encode()).hexdigest()}
