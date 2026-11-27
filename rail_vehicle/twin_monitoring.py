"""Residual screening and explicit suspension hypotheses on paired timelines."""

import numpy as np

from rail_vehicle.coupled_vehicle import simulate
from rail_vehicle.twin_data import compare, digest
from rail_vehicle.twin_identification import coefficients
from rail_vehicle.vibration import MAX_INTERVAL_DEVIATION, MIN_SPECTRAL_SAMPLES, signal_libraries, VibrationUnavailableError


def residual_profile(pair, threshold):
    if type(threshold) not in (int,float) or not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("残差阈值须为有限正数。")
    t=np.asarray(pair["time_s"])
    e=np.asarray(pair["calculated"])-np.asarray(pair["observed"])
    lines=np.asarray(pair["csv_lines"])
    triggered=np.abs(e)>threshold
    duration=t[-1]-t[0]
    windows=[]
    for bucket in range(4):
        lower,upper=t[0]+bucket*duration/4,t[0]+(bucket+1)*duration/4
        mask=(t>=lower)&((t<upper) if bucket!=3 else (t<=upper))
        if mask.any():
            windows.append({"start_s":float(t[mask][0]),"end_s":float(t[mask][-1]),"samples":int(np.sum(mask)),
                            "rms":float(np.sqrt(np.mean(e[mask]**2))),"flagged":int(np.sum(triggered[mask]))})
    delta=np.diff(t)
    period=np.median(delta)
    spectrum=None
    reason=None
    if len(e)<MIN_SPECTRAL_SAMPLES or np.max(abs(delta/period-1))>MAX_INTERVAL_DEVIATION+1e-9:
        reason="频谱至少需要 64 点且原始采样间隔最大偏差不超过 1%。"
    else:
        try:
            _,welch=signal_libraries()
            hz,psd=welch(e,fs=1/period,nperseg=min(len(e),256))
            spectrum={"frequency_hz":hz.tolist(),"psd":psd.tolist(),"resolution_hz":float(hz[1]-hz[0])}
        except VibrationUnavailableError as exc:
            reason=str(exc)
    peak=int(np.argmax(abs(e)))
    return {"threshold":float(threshold),"rule":"absolute_residual_strictly_greater","windows":windows,
            "peak":{"time_s":float(t[peak]),"csv_line":int(lines[peak]),"signed_residual":float(e[peak])},
            "flagged_csv_lines":lines[triggered].tolist(),"spectrum":spectrum,"spectrum_exclusion":reason}


def suspension_hypotheses(case,data,channel):
    """Probe two candidate changes without treating the closest fit as a diagnosis."""
    from rail_vehicle.twin_identification import VARIABLES

    frames=[("名义工况",case)]
    for label,key in (("二系横向刚度降低 20%","secondary_y_k_n_m"),
                      ("二系横向阻尼降低 20%","secondary_y_c_ns_m")):
        proposed=.8*getattr(case.transverse,key)
        if proposed>=VARIABLES[key][1]:
            frames.append((label,coefficients(case,{key:proposed})))
    evidence=[]
    for label,candidate in frames:
        result=simulate(candidate,substeps=10,include_modes=False)
        comparison=compare(data,result)
        if channel not in comparison["channels"]:
            raise ValueError("指定通道没有可比较的观测。")
        evidence.append({"scenario":label,"evidence_id":result["evidence_id"],
                         "contact_check":result["min_linear_contact_n"]>0,
                         "residual":comparison["channels"][channel]["metrics"]})
    report={"schema":"rail.twin-residual-hypotheses.v1","source":data.evidence(),
            "channel":channel,"scenarios":evidence,"claim_scope":"teaching_hypotheses_only"}
    return {**report,"evidence_id":"residual."+digest(report)[:20]}
