"""Bounded identification of 19-DOF transverse suspension coefficients."""

from dataclasses import replace
from math import isfinite

import numpy as np

from rail_vehicle.coupled_vehicle import MODEL, OUTPUT_HZ, case_config, force_at, matrices
from rail_vehicle.linear_dynamics import newmark_response
from rail_vehicle.twin_data import CHANNELS, check_case, digest, quality, residual_metrics

VARIABLES = {
    "secondary_y_k_n_m": ("二系横向刚度", 30_000., 900_000.),
    "secondary_y_c_ns_m": ("二系横向阻尼", 500., 100_000.),
    "antiroll_k_nm_rad": ("等效抗侧滚刚度", 0., 1_000_000.),
}
FIT_CHANNELS = ("body_y_accel", "body_roll", "secondary_front_y", "secondary_rear_y")
SCALE = {"secondary_y_k_n_m": 180_000., "secondary_y_c_ns_m": 16_000., "antiroll_k_nm_rad": 100_000.}


def coefficients(case, values):
    return replace(case, transverse=replace(case.transverse, **values))


def parameter_bounds(bounds):
    if not isinstance(bounds, dict) or not bounds or not set(bounds) <= VARIABLES.keys():
        raise ValueError("请选择可辨识的悬挂参数。")
    ordered = {}
    for key in VARIABLES.keys() & bounds.keys():
        interval = bounds[key]
        _, low, high = VARIABLES[key]
        if (not isinstance(interval, (tuple,list)) or len(interval) != 2
                or any(type(v) not in (int,float) or not isfinite(v) for v in interval)
                or not low <= interval[0] < interval[1] <= high):
            raise ValueError(f"{VARIABLES[key][0]}的上下限超出模型范围。")
        ordered[key] = tuple(float(v) for v in interval)
    return {key:ordered[key] for key in VARIABLES if key in ordered}


class _TransverseExperiment:
    """Reuse the exact decoupled transverse block and its fixed excitation."""

    def __init__(self, case, observations, channels, substeps=5):
        self.case, self.observations, self.substeps = case, observations, substeps
        self.dt = 1/(OUTPUT_HZ*substeps)
        nodes = np.arange(case.duration_s*OUTPUT_HZ*substeps+1)*self.dt
        self.time = nodes[::substeps]
        self.inputs = force_at(case, nodes, matrices(case)[3])[0][:,10:].copy()
        self.channels = {}
        for key in channels:
            series = observations.channels[key]
            stamps = np.asarray(series.time)
            mask = (stamps >= -1e-9) & (stamps <= self.time[-1]+1e-9)
            obs = np.asarray(series.values)[mask]
            if len(obs) < 50:
                raise ValueError(f"{key} 在配对时窗内至少需要 50 个观测点。")
            self.channels[key] = (stamps[mask], obs, max(float(np.std(obs)), CHANNELS[key][3]))

    def evaluate(self, values):
        m,c,k,_,secondary = matrices(coefficients(self.case, values))
        q,_,ddq = newmark_response(m[10:,10:],c[10:,10:],k[10:,10:],self.inputs,self.dt,self.substeps)
        s = q@secondary[:,10:].T
        bank = {"body_y_accel":ddq[:,0], "body_roll":q[:,1], "secondary_front_y":s[:,0], "secondary_rear_y":s[:,1]}
        return {key:np.interp(stamps, self.time, bank[key]) for key,(stamps,_,_) in self.channels.items()}

    def error(self, predictions):
        return np.concatenate([(predictions[key]-obs)/scale for key,(_,obs,scale) in self.channels.items()])

    def metrics(self, predictions):
        return {key:residual_metrics(obs, predictions[key]) for key,(_,obs,_) in self.channels.items()}


def identify(case, observations, validation_case, validation, bounds, *, budget=84, checkpoint=lambda: None):
    """Fit two bounded starts and evaluate a separate observation run on half-step integration."""
    try:
        from scipy.optimize import least_squares
    except ImportError:
        raise ValueError("参数辨识需要项目的 signal 可选依赖（SciPy）。") from None
    bounds = parameter_bounds(bounds)
    if type(budget) is not int or not 24 <= budget <= 180:
        raise ValueError("辨识预算须为 24 至 180 次模型计算。")
    if (observations.source != validation.source or observations.vehicle_ref!=validation.vehicle_ref
            or observations.csv_sha256 == validation.csv_sha256 or observations.record == validation.record):
        raise ValueError("辨识与验证须使用同车同类数据的不同记录。")
    check_case(observations, case)
    check_case(validation, validation_case)
    if (not case.lateral_m or not validation_case.lateral_m or case.track == "level" or validation_case.track == "level"):
        raise ValueError("辨识与独立验证均需要明确的非零横向激励。")
    if case.vehicle != validation_case.vehicle or case.transverse != validation_case.transverse:
        raise ValueError("训练与验证须采用同一车辆的初始模型参数。")
    common = [key for key in FIT_CHANNELS if key in observations.channels and key in validation.channels]
    if "body_y_accel" not in common or ("antiroll_k_nm_rad" in bounds and "body_roll" not in common):
        raise ValueError("需要两份车体横向加速度记录，辨识抗侧滚刚度还需要同步的车体侧滚角。")
    for data in (observations, validation):
        for key in common:
            info = quality(data.channels[key])
            if info["inferred_hz"] < 20 or info["jitter_fraction_p95"] > .35:
                raise ValueError(f"{key} 需要至少 20 Hz 的采样率和可复核的时钟质量。")
    training = _TransverseExperiment(case, observations, common)
    testing = _TransverseExperiment(validation_case, validation, common, substeps=10)
    if any(np.std(training.channels[key][1]) < CHANNELS[key][3]/4 for key in common):
        raise ValueError("观测响应缺少足够变化，请核对激励、通道分辨率或安装位置。")

    keys = tuple(bounds)
    unit = np.array([SCALE[key] for key in keys])
    lower, upper = np.asarray(list(bounds.values())).T/unit
    original = np.array([getattr(case.transverse, key) for key in keys])/unit
    first = np.clip(original, lower+1e-6, upper-1e-6)
    second = lower+.7*(upper-lower)
    calls = 0

    def unpack(x):
        return dict(zip(keys, map(float,x*unit), strict=True))

    def execute(experiment, x):
        nonlocal calls
        checkpoint()
        if calls >= budget:
            raise TimeoutError("已用完辨识计算预算。请缩减变量或提高预算。")
        calls += 1
        return experiment.evaluate(unpack(x))

    def fit(start, allowance):
        start_calls = calls
        def error(x):
            if calls-start_calls >= allowance:
                raise StopIteration
            result = training.error(execute(training,x))
            score = float(result@result)
            if score < best[0]:
                best[:] = [score, x.copy()]
            return result
        best = [float("inf"), start.copy()]
        try:
            answer = least_squares(error, start, bounds=(lower,upper), diff_step=2e-4, xtol=2e-4, ftol=2e-5,
                                   gtol=1e-6, max_nfev=allowance)
        except StopIteration:
            return best[1], False
        return (answer.x, answer.success) if answer.success else (best[1], False)

    reserved = 4+2*len(keys)
    allowance = (budget-reserved)//2
    a, converged_a = fit(first,allowance)
    b, converged_b = fit(second,allowance)
    trial_a,trial_b = training.error(execute(training,a)),training.error(execute(training,b))
    if trial_a@trial_a <= trial_b@trial_b:
        estimate, residual, converged = a, trial_a, converged_a
    else:
        estimate, residual, converged = b, trial_b, converged_b
    baseline_validation, result_validation = execute(testing,original),execute(testing,estimate)
    sensitivity = []
    for j in range(len(keys)):
        x1,x2 = estimate.copy(),estimate.copy()
        delta = max(1e-5, .001*(upper[j]-lower[j]))
        x1[j],x2[j] = max(lower[j],estimate[j]-delta),min(upper[j],estimate[j]+delta)
        sensitivity.append((training.error(execute(training,x2))-training.error(execute(training,x1)))/(x2[j]-x1[j]))
    jacobian = np.asarray(sensitivity).T
    _, singular, vh = np.linalg.svd(jacobian, full_matrices=False)
    rank = int(np.sum(singular > max(1e-6, singular[0]*.001)))
    condition = float(singular[0]/singular[-1]) if singular[-1] > 1e-9 else None
    angles = float(np.max(abs(a-b)/(upper-lower)))
    sigma = float(np.sqrt(np.mean(residual**2)))
    test_rms = float(np.sqrt(np.mean(testing.error(result_validation)**2)))
    original_rms = float(np.sqrt(np.mean(testing.error(baseline_validation)**2)))
    flags = []
    if rank < len(keys) or condition is None or condition > 400:
        flags.append("weak_identifiability")
    if not converged or angles > .16:
        flags.append("starts_disagree_or_unconverged")
    if np.min(np.minimum(estimate-lower,upper-estimate)/(upper-lower)) < .004:
        flags.append("bound_active")
    if test_rms > min(.30, max(.12, 1.06*original_rms)):
        flags.append("poor_independent_validation")
    intervals = None
    if not flags:
        covariance = (vh.T*(1/singular**2))@vh
        halfwidth = 1.96*np.sqrt(np.diag(covariance)*(residual@residual)/(len(residual)-len(keys)))
        if np.any(halfwidth/(upper-lower) > .25):
            flags.append("wide_uncertainty")
        else:
            intervals = {k:[float((estimate[j]-halfwidth[j])*unit[j]),float((estimate[j]+halfwidth[j])*unit[j])]
                         for j,k in enumerate(keys)}
    report = {"schema":"rail.twin-identification.v1","model":MODEL,"source":observations.source,
              "training":observations.evidence(),"validation":validation.evidence(),"case":case_config(case),
              "validation_case":case_config(validation_case),"bounds":bounds,"budget":budget,"evaluations":calls,
              "channels":common,"rank":rank,"condition":condition,"start_spread":angles,"flags":flags,
              "fit_normalized_rmse":sigma,"validation_normalized_rmse":test_rms,"baseline_normalized_rmse":original_rms,
              "validation_baseline":testing.metrics(baseline_validation),"validation_fitted":testing.metrics(result_validation),
              "identified":None if flags else unpack(estimate),"conditional_95_interval":intervals,
              "solver":{"method":"bounded two-start least squares","fit_step_ms":1.,"validation_step_ms":.5}}
    report["evidence_id"] = "identification."+digest(report)[:20]
    return report
