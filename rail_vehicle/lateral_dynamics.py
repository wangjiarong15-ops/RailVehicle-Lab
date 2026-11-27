"""Teaching model of carbody lateral translation and roll on a prescribed bogie."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite, pi

import numpy as np

from rail_vehicle.bogie_model_spec import DIMENSIONS
from rail_vehicle.linear_dynamics import newmark_response
from rail_vehicle.vehicle_dynamics import modal_properties


MODEL = "rail.carbody-lateral-roll.v1"
TRACKS = {"level": "零横向输入", "sine": "周期横向支承输入", "bump": "局部横向支承输入"}
SAMPLE_HZ = 200
HALF_SPAN_M = DIMENSIONS["side_beam_centres"] / 2


@dataclass(frozen=True)
class LateralCase:
    track: str = "bump"
    speed_m_s: float = 20.
    amplitude_m: float = .008
    wavelength_m: float = 10.
    duration_s: int = 6
    mass_kg: float = 9000.
    roll_inertia_kg_m2: float = 12000.
    com_height_m: float = .85
    lateral_k_n_m: float = 180000.
    lateral_c_ns_m: float = 16000.
    vertical_k_each_n_m: float = 220000.
    vertical_c_each_ns_m: float = 11000.
    anti_roll_k_nm_rad: float = 100000.

    def __post_init__(self):
        if not isinstance(self.track, str) or self.track not in TRACKS or type(self.duration_s) is not int or not 1 <= self.duration_s <= 15:
            raise ValueError("不支持的激励类型或时长（1 至 15 秒）。")
        bounds = (
            (self.speed_m_s, 2, 60), (self.amplitude_m, 0, .02), (self.wavelength_m, 2, 60),
            (self.mass_kg, 3000, 20000), (self.roll_inertia_kg_m2, 3000, 45000), (self.com_height_m, .3, 1.5),
            (self.lateral_k_n_m, 60000, 800000), (self.lateral_c_ns_m, 2000, 60000),
            (self.vertical_k_each_n_m, 60000, 600000), (self.vertical_c_each_ns_m, 1000, 50000),
            (self.anti_roll_k_nm_rad, 0, 600000),
        )
        if not all(type(value) in (int, float) and low <= value <= high and isfinite(value)
                   for value, low, high in bounds):
            raise ValueError("横向与侧滚参数超出教学模型范围。")


def excitation(case, time):
    """Prescribed bogie y and dy/dt, defined on travelled distance x = v t."""
    s = np.asarray(time) * case.speed_m_s / case.wavelength_m
    rate = case.speed_m_s / case.wavelength_m
    amplitude = case.amplitude_m
    if case.track == "level":
        return np.zeros_like(s), np.zeros_like(s)
    if case.track == "bump":
        supported = (s >= 0) & (s < 1)
        return (np.where(supported, amplitude/2*(1-np.cos(2*pi*s)), 0),
                np.where(supported, amplitude*pi*rate*np.sin(2*pi*s), 0))
    ramp = np.clip(s, 0, 1)
    gain = (1-np.cos(pi*ramp))/2
    gain_rate = np.where((s > 0) & (s < 1), pi*rate/2*np.sin(pi*ramp), 0)
    return (amplitude*gain*np.sin(2*pi*s),
            amplitude*(gain*2*pi*rate*np.cos(2*pi*s)+gain_rate*np.sin(2*pi*s)))


def matrices(case):
    arm = np.array([1., case.com_height_m])
    coupling = np.outer(arm, arm)
    rotational_k = 2 * HALF_SPAN_M**2 * case.vertical_k_each_n_m + case.anti_roll_k_nm_rad
    rotational_c = 2 * HALF_SPAN_M**2 * case.vertical_c_each_ns_m
    return (np.diag([case.mass_kg, case.roll_inertia_kg_m2]),
            case.lateral_c_ns_m*coupling + np.diag([0., rotational_c]),
            case.lateral_k_n_m*coupling + np.diag([0., rotational_k]), arm)


def case_config(case):
    return {"schema": "rail.lateral-experiment.v1", "model": MODEL, "case": asdict(case),
            "parameter_source": "teaching"}


def read_case(content):
    if len(content) > 16_384:
        raise ValueError("横向工况文件须小于 16 KiB。")
    try:
        config = json.loads(content)
        active = LateralCase(**config["case"])
        if config != case_config(active):
            raise ValueError
        return active
    except (TypeError, KeyError, ValueError, RecursionError):
        raise ValueError("无法读取该版本的横向教学工况。") from None


def simulate_lateral(case, *, substeps=5):
    if type(substeps) is not int or not 5 <= substeps <= 40:
        raise ValueError("积分子步数应为 5 至 40。")
    mass, damping, stiffness, arm = matrices(case)
    dt = 1/(SAMPLE_HZ*substeps)
    times = np.arange(case.duration_s*SAMPLE_HZ*substeps+1)*dt
    base, base_velocity = excitation(case, times)
    force = (case.lateral_k_n_m*base + case.lateral_c_ns_m*base_velocity)[:, None]*arm
    q, dq, ddq = newmark_response(mass, damping, stiffness, force, dt, substeps)
    config = {**case_config(case), "substeps": substeps}
    digest = sha256(json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {**config, "source": "calculated", "evidence_id": f"simulation.{digest[:16]}",
            "configuration_sha256": digest, "sample_hz": SAMPLE_HZ, "time_s": times[::substeps].tolist(),
            "dofs": ["carbody_y", "carbody_roll_x"], "units_q": ["m", "rad"],
            "q": q.tolist(), "dq": dq.tolist(), "ddq": ddq.tolist(), "base_y_m": base[::substeps].tolist(),
            "mount_relative_y_m": (q@arm-base[::substeps]).tolist(),
            "air_spring_vertical_m": (q[:, 1, None]*np.array([-HALF_SPAN_M, HALF_SPAN_M])).tolist(),
            "modes": modal_properties(mass, damping, stiffness)}


def lateral_summary(result):
    q, ddq = map(np.asarray, (result["q"], result["ddq"]))
    return {"body_y_peak_mm": float(1000*np.max(np.abs(q[:, 0]))),
            "roll_peak_mrad": float(1000*np.max(np.abs(q[:, 1]))),
            "body_y_rms_m_s2": float(np.sqrt(np.mean(ddq[:, 0]**2))),
            "evidence_id": result["evidence_id"]}


def lateral_playback(result, gain=1):
    if type(gain) is not int or gain not in (1, 2, 4):
        raise ValueError("未知动画倍率。")
    q, ddq, times = map(np.asarray, (result["q"], result["ddq"], result["time_s"]))
    return {"lateral_model": MODEL, "sample_hz": result["sample_hz"],
            "parameters": {"gain": gain, "evidence_id": result["evidence_id"], "com_height_m": result["case"]["com_height_m"]},
            "channels": {"body_y_m": q[:, 0].tolist(), "roll_rad": q[:, 1].tolist(),
                         "base_y_m": result["base_y_m"], "accel_m_s2": ddq[:, 0].tolist(),
                         "wheel_rad": (times*result["case"]["speed_m_s"]/DIMENSIONS["tread_radius"]).tolist(),
                         "travel_m": result["mount_relative_y_m"], "spring_m": result["air_spring_vertical_m"]}}
