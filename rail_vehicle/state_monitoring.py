"""Observable vertical component of the twin, with causal estimation and local hypotheses."""

from dataclasses import dataclass, replace, asdict
from functools import lru_cache
from hashlib import sha256
from math import sin, tau

import numpy as np

from rail_vehicle.measurement_stream import Acquisition, Device, Packet, STEP_US
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import in_range

MODEL = "rail.vertical-state.v1"
DT = STEP_US / 1e6


@dataclass(frozen=True)
class ObserverCase:
    mass_kg: float = 1500.
    stiffness_n_m: float = 110_000.
    damping_ns_m: float = 9000.
    process_psd: float = .08
    displacement_sigma_m: float = .0012
    acceleration_sigma_m_s2: float = .08

    def __post_init__(self):
        limits=((self.mass_kg,200,6000),(self.stiffness_n_m,20_000,600_000),
                (self.damping_ns_m,300,45_000),(self.process_psd,1e-7,10),
                (self.displacement_sigma_m,1e-6,.03),(self.acceleration_sigma_m_s2,.0001,5))
        if not all(in_range(*item) for item in limits):
            raise ValueError("状态估计模型参数或噪声超出范围。")


@lru_cache(maxsize=48)
def matrices(case):
    from scipy.linalg import expm

    h_acc=np.array([-case.stiffness_n_m/case.mass_kg,-case.damping_ns_m/case.mass_kg])
    continuous=np.array([[0,1],h_acc])
    b=np.array([[0.],[1/case.mass_kg]])
    augmented=np.zeros((3,3))
    augmented[:2,:2],augmented[:2,2:]=continuous,b
    step=expm(augmented*DT)
    process=np.diag([0,case.process_psd])
    van_loan=expm(np.block([[continuous,process],[np.zeros((2,2)),-continuous.T]])*DT)
    q=van_loan[:2,2:]@step[:2,:2].T
    return step[:2,:2],step[:2,2],(q+q.T)/2,h_acc


def estimate(acquisition: Acquisition, case=ObserverCase()):
    a,b,q,h_acc=matrices(case)
    x=np.zeros(2)
    model=np.zeros(2)
    p=np.diag([.012**2,.12**2])
    cov0=p.copy()
    previous_force=None
    gaps=0
    rows=[]
    observed=[]
    powers=[np.linalg.matrix_power(a,i) for i in range(25)]
    if not acquisition.samples:
        return rows
    for tick in range(max(acquisition.samples)+1):
        measurements=acquisition.samples.get(tick,{})
        force=measurements.get("force")
        if tick and previous_force is not None:
            model=a@model+b*previous_force
            x=a@x+b*previous_force
            p=a@p@a.T+q
        if force is None:
            gaps+=1
        else:
            gaps=0
            previous_force=force
        if gaps>3:
            previous_force=None
            observed.clear()
            x[:]=0
            model[:]=0
            p=cov0.copy()
        row={"time_s":tick*DT,"force_n":force,"observed_m":measurements.get("displacement"),
             "observed_accel":measurements.get("acceleration"),"predicted_m":None,"estimate_m":None,
             "estimate_velocity_m_s":None,"sigma_m":None,"innovation_accel":None,"standard_residual":None,
             "status":"input_gap" if previous_force is None else "waiting"}
        if previous_force is not None:
            if gaps==0:
                row["predicted_m"]=float(model[0])
            candidates=[("displacement",np.array([1.,0.]),case.displacement_sigma_m,0.)]
            # An acceleration update also needs force at that exact sample.
            if force is not None:
                candidates.append(("acceleration",h_acc,case.acceleration_sigma_m_s2,force/case.mass_kg))
            for name,h,std,offset in candidates:
                if name not in measurements:
                    continue
                innovation=measurements[name]-(h@x+offset)
                variance=float(h@p@h+std**2)
                normalized=float(innovation/np.sqrt(variance))
                if name=="acceleration":
                    row["innovation_accel"]=float(innovation)
                    row["standard_residual"]=normalized
                if abs(normalized)>5:
                    continue
                weight=p@h/variance
                x+=weight*innovation
                correction=np.eye(2)-np.outer(weight,h)
                p=correction@p@correction.T+np.outer(weight,weight)*std**2
                observed.append((tick,h))
            observed=[(t,h) for t,h in observed if tick-t<25]
            # Consecutive acceleration rows may establish rank. Missing rows cannot.
            if len(observed)>=2:
                origin=observed[0][0]
                table=np.array([h@powers[t-origin] for t,h in observed])
                table/=np.linalg.norm(table,axis=1)[:,None]
                if np.linalg.matrix_rank(table,tol=.002)==2 and np.sqrt(p[0,0])<.020:
                    row.update(estimate_m=float(x[0]),estimate_velocity_m_s=float(x[1]),
                               sigma_m=float(np.sqrt(p[0,0])),status="filtered" if candidates and any(
                                   t==tick for t,_ in observed) else "prediction_only")
        rows.append(row)
    return rows


def simulated_stream(hypothesis="damping", count=320, seed=214):
    if hypothesis not in ("nominal","damping","stiffness","bias") or not 20<=count<=1000:
        raise ValueError("模拟工况或样点数量无效。")
    rng=np.random.default_rng(seed)
    devices=(Device("drive","force","kN",1,1.,0.,0,"declared","common_trigger"),
             Device("stroke","displacement","mm",1,1.,.25,1200,"declared","common_trigger"),
             Device("imu-z","acceleration","g",-1,1.,.015,-700,"declared","common_trigger"))
    model=ObserverCase()
    actual=replace(model,stiffness_n_m=model.stiffness_n_m*(.78 if hypothesis=="stiffness" else 1),
                   damping_ns_m=model.damping_ns_m*(.62 if hypothesis=="damping" else 1))
    x=np.zeros(2)
    packets=[]
    truth=[]
    origin=1_750_000_000_000_000
    a,b,_,h=matrices(actual)
    drive=lambda t:2200*sin(tau*.85*t)+1450*sin(tau*1.42*t)+650*sin(tau*2.15*t)
    for tick in range(count):
        force=drive(tick*DT)
        acceleration=h@x+force/actual.mass_kg
        truth.append(float(x[0]))
        data=(force,x[0]+rng.normal(0,.0012),
              acceleration+rng.normal(0,.08)+(.24 if hypothesis=="bias" else 0.))
        for device,value in zip(devices,data,strict=True):
            if device.channel!="force" and (tick in (33,75,76) or (tick==190 and device.channel=="displacement")):
                continue
            encoded=float(value/(device.sign*{"kN":1000,"mm":.001,"g":9.80665}[device.unit])+device.bias)
            stamp=origin+tick*STEP_US+device.clock_offset_us+int(rng.integers(-400,401))
            packets.append((tick,Packet(device.device,tick,stamp,encoded)))
        x=a@x+b*force
    return Acquisition(devices,origin,"simulated"),tuple(packets),truth


def diagnosis(acquisition, case=ObserverCase()):
    """Score declared competing models on the final half of the observation record."""
    from scipy.signal import welch

    counters=dict(acquisition.counters)
    evidence={"schema":"rail.monitoring.v1","model":MODEL,"source":acquisition.source,
              "recording":acquisition.evidence_id,"quality":counters,"settings":asdict(case)}
    samples=acquisition.samples
    n=max(samples,default=-1)+1
    held_out=n//2
    initial_evidence={**evidence,"window_start_s":held_out*DT,"attempts":[],"frequency_hz":None}
    if (held_out<80 or counters["unsynchronized"] or counters["invalid_range"]
            or any("force" not in samples.get(i,{}) for i in range(n))):
        result={**initial_evidence,"status":"insufficient","next_action":"核查共同触发、设备量程及完整激励，再比较悬挂假设。"}
    else:
        time_keys=[tick for tick in range(n) if "acceleration" in samples.get(tick,{})]
        tail=[tick for tick in time_keys if tick>=held_out]
        if len(tail)<.8*(n-held_out):
            result={**initial_evidence,"status":"insufficient","next_action":"补采同一时基下的垂向加速度。"}
        else:
            observed=np.array([samples[tick]["acceleration"] for tick in time_keys])
            hypotheses=(("名义参数",case),("等效刚度降低 22%",replace(case,stiffness_n_m=case.stiffness_n_m*.78)),
                        ("等效阻尼降低 38%",replace(case,damping_ns_m=case.damping_ns_m*.62)),
                        ("加速度零偏",case),("激励比例偏差",case))
            scores=[]
            for name,candidate in hypotheses:
                a,b,_,h=matrices(candidate)
                x=np.zeros(2)
                predicted=[]
                for tick in range(n):
                    force=samples[tick]["force"]
                    predicted.append(float(h@x+force/case.mass_kg))
                    x=a@x+b*force
                baseline=np.asarray(predicted)[time_keys]
                train=np.array(time_keys)<held_out
                if name=="加速度零偏":
                    baseline+=float(np.clip(np.mean((observed-baseline)[train]),-.8,.8))
                if name=="激励比例偏差":
                    fit=baseline[train]
                    if fit@fit>1e-9:
                        baseline*=np.clip((fit@observed[train])/(fit@fit),.7,1.3)
                error=observed[~train]-baseline[~train]
                scores.append({"hypothesis":name,"rmse_m_s2":float(np.sqrt(np.mean(error**2))),"samples":len(error)})
            scores.sort(key=lambda item:item["rmse_m_s2"])
            contiguous=max(np.split(np.asarray(time_keys),np.flatnonzero(np.diff(time_keys)>1)+1),key=len)
            spectrum=None
            if len(contiguous)>=64:
                trace=np.asarray([samples[int(i)]["acceleration"] for i in contiguous])
                freq,psd=welch(trace,fs=1/DT,nperseg=min(128,len(contiguous)))
                spectrum=float(freq[1+np.argmax(psd[1:])])
            resolved=scores[1]["rmse_m_s2"]-scores[0]["rmse_m_s2"]>case.acceleration_sigma_m_s2
            result={**initial_evidence,"attempts":scores,"frequency_hz":spectrum,
                    "status":"candidate" if resolved else "unresolved",
                    "next_action":"在独立激励下重复试验，并用同步行程、标定和参考力检查候选及替代解释。"}
    return {**result,"evidence_id":"monitoring."+sha256(json_text(result).encode()).hexdigest()}
