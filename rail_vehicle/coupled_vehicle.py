"""Symmetric linear teaching vehicle about static equilibrium (19 coordinates)."""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from math import isfinite, pi, tau
from random import Random

import numpy as np

from rail_vehicle.bogie_model_spec import DIMENSIONS
from rail_vehicle.linear_dynamics import newmark_response
from rail_vehicle.vehicle_dynamics import (DOFS as VERTICAL, OUTPUT_HZ, VehicleParameters,
                                            axle_distances, matrices as vertical_matrices, modal_properties)

MODEL = "rail.vehicle-coupled.v1"
SCHEMA = "rail.coupled-experiment.v1"
TRACKS = {"level": "零输入", "sine": "正弦不平顺", "random": "有种子随机谱", "bump": "局部余弦脉冲", "custom": "自定义空间轨迹"}
DOFS = (*VERTICAL, *(f"{body}_{axis}" for body in ("body", "front_bogie", "rear_bogie") for axis in ("y", "roll", "yaw")))
UNITS = ["rad" if key.endswith(("pitch", "roll", "yaw")) else "m" for key in DOFS]


def _range(value, lower, upper):
    return type(value) in (int, float) and isfinite(value) and lower <= value <= upper


@dataclass(frozen=True)
class TransverseParameters:
    body_roll_inertia: float = 65_000.
    body_yaw_inertia: float = 1_500_000.
    bogie_roll_inertia: float = 2200.
    bogie_yaw_inertia: float = 3600.
    body_mount_height_m: float = .85
    bogie_upper_height_m: float = .45
    bogie_lower_height_m: float = .35
    primary_y_k_n_m: float = 550_000.
    primary_y_c_ns_m: float = 22_000.
    secondary_y_k_n_m: float = 180_000.
    secondary_y_c_ns_m: float = 16_000.
    antiroll_k_nm_rad: float = 100_000.
    secondary_yaw_k_nm_rad: float = 150_000.
    secondary_yaw_c_nms_rad: float = 12_000.

    def __post_init__(self):
        limits = ((8000, 250000), (100000, 6000000), (250, 15000), (500, 30000),
                  (.15, 1.5), (.1, .8), (.1, .8), (80000, 2e6), (1000, 100000), (30000, 900000),
                  (500, 100000), (0, 1e6), (0, 4e6), (0, 2e5))
        if not all(_range(v, *limit) for v, limit in zip(asdict(self).values(), limits, strict=True)):
            raise ValueError("横向、侧滚或摇头参数超出教学模型范围。")


@dataclass(frozen=True)
class CoupledCase:
    vehicle: VehicleParameters = field(default_factory=VehicleParameters)
    transverse: TransverseParameters = field(default_factory=TransverseParameters)
    track: str = "bump"
    speed_m_s: float = 20.
    lateral_m: float = .008
    vertical_m: float = .003
    length_m: float = 8.
    duration_s: int = 6
    seed: int = 42
    # (distance in metres, lateral fraction, vertical fraction), immutable for cache identity.
    profile: tuple = ()

    def __post_init__(self):
        if (not isinstance(self.vehicle, VehicleParameters) or not isinstance(self.transverse, TransverseParameters)
                or not isinstance(self.track, str) or self.track not in TRACKS
                or type(self.duration_s) is not int or not 1 <= self.duration_s <= 12
                or type(self.seed) is not int or not 0 <= self.seed < 2**32):
            raise ValueError("模型、激励、时长或随机种子无效。")
        if not all(_range(v, lo, hi) for v, lo, hi in ((self.speed_m_s, 2, 70), (self.lateral_m, 0, .02),
                   (self.vertical_m, 0, .012), (self.length_m, 2, 40))):
            raise ValueError("速度、幅值或空间波长超出教学范围。")
        if type(self.profile) is not tuple or (self.track != "custom" and self.profile):
            raise ValueError("自定义空间点仅用于自定义工况。")
        if self.track == "custom":
            if not 2 <= len(self.profile) <= 256 or not all(type(row) is tuple and len(row) == 3 and
                    _range(row[0], 0, 1000) and _range(row[1], -1, 1) and _range(row[2], -1, 1) for row in self.profile):
                raise ValueError("自定义空间轨迹需要 2–256 个有限的 [距离, 横向比例, 垂向比例] 点。")
            if (self.profile[0] != (0, 0, 0) or self.profile[-1][1:] != (0, 0)
                    or any(right[0]-left[0] < .25 for left, right in zip(self.profile, self.profile[1:]))):
                raise ValueError("轨迹从距离零开始，两端位移为零，相邻距离至少 0.25 m。")


def case_config(case):
    return {"schema": SCHEMA, "model": MODEL, "parameter_source": "teaching", "case": asdict(case)}


def read_case(content):
    if not isinstance(content, (bytes, str)) or len(content) > 32768:
        raise ValueError("工况文件须小于 32 KiB。")
    try:
        data = json.loads(content)
        if set(data) != {"schema", "model", "parameter_source", "case"} or (
                data["schema"], data["model"], data["parameter_source"]) != (SCHEMA, MODEL, "teaching"):
            raise ValueError
        values = data["case"].copy()
        values["vehicle"] = VehicleParameters(**values["vehicle"])
        values["transverse"] = TransverseParameters(**values["transverse"])
        values["profile"] = tuple(tuple(row) for row in values["profile"])
        return CoupledCase(**values)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        raise ValueError("无法读取该版本的整车教学工况。") from None


def spatial_input(case, x):
    """Return lateral y, dy/dt and vertical z at axle travel distances x."""
    x = np.asarray(x, dtype=float)
    if case.track == "level":
        return tuple(np.zeros_like(x, dtype=float) for _ in range(3))
    if case.track == "custom":
        distance, lateral, vertical = np.asarray(case.profile).T
        index = np.clip(np.searchsorted(distance, x, side="right")-1, 0, len(distance)-2)
        active = (x >= 0) & (x < distance[-1])
        slope = np.diff(lateral)/np.diff(distance)
        return (case.lateral_m*np.interp(x, distance, lateral, left=0, right=0),
                case.lateral_m*case.speed_m_s*np.where(active, slope[index], 0),
                case.vertical_m*np.interp(x, distance, vertical, left=0, right=0))
    u = x/case.length_m
    if case.track == "bump":
        active = (u > 0) & (u < 1)
        shape = np.where(active, (1-np.cos(tau*u))/2, 0)
        derivative = np.where(active, pi*np.sin(tau*u), 0)
    else:
        ramp = np.clip(u, 0, 1)
        envelope = (1-np.cos(pi*ramp))/2
        envelope_derivative = np.where((u > 0) & (u < 1), pi*np.sin(pi*ramp)/2, 0)
        if case.track == "sine":
            wave, slope = np.sin(tau*u), tau*np.cos(tau*u)
        else:
            random = Random(case.seed)
            phases = [tau*f*u + random.uniform(0, tau) for f in (1, 2, 3, 4)]
            norm = sum(1/f for f in (1, 2, 3, 4))
            wave = sum(np.sin(phase)/f for f, phase in enumerate(phases, 1))/norm
            slope = tau*sum(np.cos(phase) for phase in phases)/norm
        shape = envelope*wave
        derivative = envelope*slope+envelope_derivative*wave
    return (case.lateral_m*shape, case.lateral_m*case.speed_m_s/case.length_m*derivative, case.vertical_m*shape)


def matrices(case):
    v, p = case.vehicle, case.transverse
    mv, cv, kv, _, _ = vertical_matrices(v)
    mass = np.diag([*np.diag(mv), v.body_mass_kg, p.body_roll_inertia, p.body_yaw_inertia,
                    *([v.bogie_mass_kg, p.bogie_roll_inertia, p.bogie_yaw_inertia]*2)])
    damping, stiffness = np.zeros_like(mass), np.zeros_like(mass)
    damping[:10, :10], stiffness[:10, :10] = cv, kv
    b1, b2 = DIMENSIONS["axlebox_centres"]/2, DIMENSIONS["side_beam_centres"]/2
    a, b = v.bogie_half_spacing_m, DIMENSIONS["wheelbase"]/2
    lateral_primary = np.zeros((4, len(DOFS)))
    lateral_secondary = np.zeros((2, len(DOFS)))

    def element(row, k, c):
        outer = np.outer(row, row)
        stiffness[:] += k*outer
        damping[:] += c*outer

    for i, sign in enumerate((1, -1)):
        y, roll, yaw = range(13+3*i, 16+3*i)
        row = lateral_secondary[i]
        row[[10, 11, 12, y, roll]] = [1, p.body_mount_height_m, sign*a, -1, p.bogie_upper_height_m]
        element(row, p.secondary_y_k_n_m, p.secondary_y_c_ns_m)
        for axis, body, k, c in ((roll, 11, v.secondary_k_n_m*b2**2+p.antiroll_k_nm_rad, v.secondary_c_ns_m*b2**2),
                                 (yaw, 12, p.secondary_yaw_k_nm_rad, p.secondary_yaw_c_nms_rad)):
            row = np.zeros(len(DOFS))
            row[[body, axis]] = [1, -1]
            element(row, k, c)
        row = np.zeros(len(DOFS))
        row[roll] = 1
        element(row, 2*v.primary_k_n_m*b1**2, 2*v.primary_c_ns_m*b1**2)
        for j, axle_sign in enumerate((1, -1)):
            row = lateral_primary[2*i+j]
            row[[y, roll, yaw]] = [1, p.bogie_lower_height_m, axle_sign*b]
            element(row, p.primary_y_k_n_m, p.primary_y_c_ns_m)
    return mass, damping, stiffness, lateral_primary, lateral_secondary


def force_at(case, times, rows):
    y, dy, z = spatial_input(case, np.asarray(times)[..., None]*case.speed_m_s-axle_distances(case.vehicle))
    p = case.transverse
    force = (p.primary_y_k_n_m*y+p.primary_y_c_ns_m*dy)@rows
    force[..., 6:10] += case.vehicle.contact_k_n_m*z
    return force, y, z


def simulate(case, *, substeps=5, include_modes=True):
    if type(substeps) is not int or not 5 <= substeps <= 40:
        raise ValueError("积分子步数必须为 5 至 40。")
    m, c, k, primary_y, secondary_y = matrices(case)
    dt = 1/(OUTPUT_HZ*substeps)
    time = np.arange(case.duration_s*OUTPUT_HZ*substeps+1)*dt
    force, rail_y, rail_z = force_at(case, time, primary_y)
    q, dq, ddq = newmark_response(m, c, k, force, dt, substeps)
    v = case.vehicle
    nominal = 9.80665*(v.body_mass_kg/4+v.bogie_mass_kg/2+v.wheelset_mass_kg)
    lowest_contact = float(np.min(nominal+v.contact_k_n_m*(rail_z[::substeps]-q[:, 6:10])))
    config = {**case_config(case), "solver": {"method": "average-acceleration-newmark", "substeps": substeps, "sample_hz": OUTPUT_HZ}}
    digest = sha256(json.dumps(config, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    return {**config, "evidence_id": "simulation."+digest[:16], "configuration_sha256": digest,
            "time_s": time[::substeps].tolist(), "dofs": list(DOFS), "units_q": UNITS,
            "q": q.tolist(), "dq": dq.tolist(), "ddq": ddq.tolist(), "rail_y_m": rail_y[::substeps].tolist(),
            "rail_z_m": rail_z[::substeps].tolist(), "secondary_y_m": (q@secondary_y.T).tolist(),
            "axle_delay_s": (axle_distances(v)/case.speed_m_s).tolist(), "min_linear_contact_n": lowest_contact,
            "modes": modal_properties(m, c, k) if include_modes else None}


def summary(result):
    q, accel, rail = (np.asarray(result[x]) for x in ("q", "ddq", "rail_y_m"))
    peak = int(np.argmax(np.abs(accel[:, 10])))
    rms_y = float(np.sqrt(np.mean(accel[:, 10]**2)))
    rms_z = float(np.sqrt(np.mean(accel[:, 0]**2)))
    input_rms = float(np.sqrt(np.mean(rail[:, 0]**2)))
    return {"evidence_id": result["evidence_id"], "body_y_rms_m_s2": rms_y, "body_z_rms_m_s2": rms_z,
            "peak_y_m_s2": float(abs(accel[peak, 10])), "peak_y_time_s": result["time_s"][peak],
            "roll_peak_mrad": float(1000*np.max(np.abs(q[:, 11]))), "yaw_peak_mrad": float(1000*np.max(np.abs(q[:, 12]))),
            "secondary_y_peak_mm": float(1000*np.max(np.abs(result["secondary_y_m"]))),
            "y_displacement_rms_ratio": float(np.sqrt(np.mean(q[:, 10]**2))/input_rms) if input_rms else None,
            "contact_assumption_exceeded": result["min_linear_contact_n"] <= 0}


def frequency_response(case, frequencies):
    """Unit lateral harmonic at the lead axle, complex speed-dependent delays downstream."""
    m, c, k, primary, _ = matrices(case)
    delay = axle_distances(case.vehicle)/case.speed_m_s
    p = case.transverse
    output = []
    for f in frequencies:
        if not _range(f, 0, 50):
            raise ValueError("频率须为 0–50 Hz。")
        w = tau*f
        force = ((p.primary_y_k_n_m+1j*w*p.primary_y_c_ns_m)*np.exp(-1j*w*delay))@primary
        q = np.linalg.solve(k + 1j*w*c - w**2*m, force)
        output.append({"frequency_hz": f, "body_y_over_input": float(abs(q[10])), "body_roll_rad_per_m": float(abs(q[11]))})
    return output


def playback(result, bogie="front", gain=1):
    """Project a common result clock into the existing GLB inspection view."""
    if bogie not in ("front", "rear") or gain not in (1, 2, 4):
        raise ValueError("未知转向架或显示倍率。")
    i = 0 if bogie == "front" else 1
    q, dq, ddq, rail_y, rail_z = (np.asarray(result[key]) for key in ("q", "dq", "ddq", "rail_y_m", "rail_z_m"))
    raw = result["case"]
    half = raw["vehicle"]["bogie_half_spacing_m"]
    mount = raw["transverse"]["body_mount_height_m"]
    # Bound displacement over the full recording, including angular motion at outer mounts.
    motion = max(np.max(np.abs(q[:, [0, 2, 4, 6, 7, 8, 9, 10, 13, 16]])),
                 np.max(np.abs(q[:, [1, 12]]))*(half+2),
                 np.max(np.abs(q[:, [3, 5, 11, 14, 15, 17, 18]]))*2,
                 np.max(np.abs(rail_y)), 1e-12)
    display_gain = min(gain, .045/float(motion))
    frame = 2 + 2*i
    wheel = 6 + 2*i
    side = 1 if i == 0 else -1
    b = DIMENSIONS["wheelbase"]/2
    front_travel = q[:, frame]+q[:, frame+1]*b-q[:, wheel]
    rear_travel = q[:, frame]-q[:, frame+1]*b-q[:, wheel+1]
    channels = {"travel_m": q[:, 10].tolist(), "roll_rad": q[:, 11].tolist(),
                "accel_m_s2": ddq[:, 10].tolist(), "body_y_m": q[:, 10].tolist(),
                "body_z_m": q[:, 0].tolist(), "body_z_accel_m_s2": ddq[:, 0].tolist(),
                "body_y_velocity_m_s": dq[:, 10].tolist(), "body_z_velocity_m_s": dq[:, 0].tolist(),
                "secondary_y_m": np.asarray(result["secondary_y_m"])[:, i].tolist(),
                "front_travel_m": front_travel.tolist(), "rear_travel_m": rear_travel.tolist(),
                "front_wheel_m": q[:, wheel].tolist(), "rear_wheel_m": q[:, wheel+1].tolist(),
                "secondary_m": (q[:, 0]+side*half*q[:, 1]-q[:, frame]).tolist(),
                "front_support_m": rail_z[:, 2*i].tolist(), "rear_support_m": rail_z[:, 2*i+1].tolist(),
                "wheel_rad": (np.asarray(result["time_s"])*raw["speed_m_s"]/DIMENSIONS["tread_radius"]).tolist()}
    return {"vehicle_model": MODEL, "sample_hz": OUTPUT_HZ, "channels": channels,
            "parameters": {"evidence_id": result["evidence_id"], "bogie": bogie, "gain": gain},
            "coupled": {"q": result["q"], "rail_y": result["rail_y_m"], "display_gain": display_gain, "index": i,
                        "half_spacing": half, "half_wheelbase": b, "body_mount": mount,
                        "upper_mount": raw["transverse"]["bogie_upper_height_m"],
                        "body_peak_sample": int(np.argmax(np.abs(ddq[:, 10])))},
            "wheelbase_m": 2*b}
