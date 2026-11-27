"""Linear 10-DOF pitch-plane vehicle experiment about static equilibrium."""

from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
from math import tau
from random import Random

import numpy as np
from rail_vehicle.linear_dynamics import newmark_response

from rail_vehicle.bogie_model_spec import DIMENSIONS


MODEL = "rail.vehicle-vertical.v1"
DOFS = ("body_z", "body_pitch", "front_bogie_z", "front_bogie_pitch",
        "rear_bogie_z", "rear_bogie_pitch", "wheel_1_z", "wheel_2_z", "wheel_3_z", "wheel_4_z")
OUTPUT_HZ = 200
TRACKS = {"sine": "正弦", "multi": "多频组合", "bump": "局部凸起", "random": "有种子随机谱"}


def _bounded(value, low, high):
    return type(value) in (float, int) and low <= value <= high


@dataclass(frozen=True)
class VehicleParameters:
    body_mass_kg: float = 32000
    body_inertia_kg_m2: float = 1_450_000
    bogie_mass_kg: float = 3000
    bogie_inertia_kg_m2: float = 3200
    wheelset_mass_kg: float = 1450
    bogie_half_spacing_m: float = 8.5
    primary_k_n_m: float = 1_200_000
    primary_c_ns_m: float = 25_000
    secondary_k_n_m: float = 650_000
    secondary_c_ns_m: float = 45_000
    contact_k_n_m: float = 90_000_000

    def __post_init__(self):
        limits = ((10000, 65000), (100000, 5000000), (1000, 6000), (500, 10000), (500, 2500),
                  (3, 12), (200000, 3000000), (1000, 80000), (100000, 3000000),
                  (1000, 150000), (10000000, 180000000))
        if not all(_bounded(value, *limit) for value, limit in zip(asdict(self).values(), limits, strict=True)):
            raise ValueError("车辆参数超出教学模型允许范围。")


@dataclass(frozen=True)
class VehicleCase:
    vehicle: VehicleParameters = field(default_factory=VehicleParameters)
    track: str = "sine"
    speed_m_s: float = 20
    amplitude_m: float = .003
    wavelength_m: float = 8
    duration_s: int = 6
    seed: int = 42

    def __post_init__(self):
        if not isinstance(self.vehicle, VehicleParameters) or self.track not in TRACKS:
            raise ValueError("未知轨道激励或车辆参数格式。")
        if not all(_bounded(value, *limit) for value, limit in (
                (self.speed_m_s, (2, 70)), (self.amplitude_m, (0, .012)), (self.wavelength_m, (2, 40)))):
            raise ValueError("速度、幅值或波长超出允许范围。")
        if type(self.duration_s) is not int or not 1 <= self.duration_s <= 20:
            raise ValueError("时长应为 1–20 秒整数。")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("随机种子应为 32 位非负整数。")


def track_height(case, x):
    """Rail profile as a function of metres travelled since the leading axle entered."""
    u = np.asarray(x, dtype=float) / case.wavelength_m
    if case.track == "bump":
        return case.amplitude_m * np.where((u > 0) & (u < 1), .5*(1-np.cos(tau*u)), 0)
    envelope = .5*(1-np.cos(np.pi*np.clip(u, 0, 1)))
    if case.track == "sine":
        profile = np.sin(tau*u)
    elif case.track == "multi":
        profile = .65*np.sin(tau*u) + .35*np.sin(2*tau*u)
    else:
        generator = Random(case.seed)
        profile = sum(np.sin(tau*f*u + generator.uniform(0, tau))/(f*sum((1, .5, 1/3, .25)))
                      for f in (1, 2, 3, 4))
    return case.amplitude_m * envelope * profile


def matrices(vehicle):
    a = vehicle.bogie_half_spacing_m
    b = DIMENSIONS["wheelbase"]/2
    mass = np.diag([vehicle.body_mass_kg, vehicle.body_inertia_kg_m2,
                    *([vehicle.bogie_mass_kg, vehicle.bogie_inertia_kg_m2]*2),
                    *([vehicle.wheelset_mass_kg]*4)])
    secondary = np.zeros((2, 10))
    primary = np.zeros((4, 10))
    for bogie, sign in enumerate((1, -1)):
        z = 2+2*bogie
        secondary[bogie, [0, 1, z]] = [1, sign*a, -1]
        for axle, end in enumerate((1, -1)):
            i = 2*bogie+axle
            primary[i, [z, z+1, 6+i]] = [1, end*b, -1]
    wheels = np.eye(10)[6:]
    p = primary.T @ primary
    s = secondary.T @ secondary
    stiffness = vehicle.primary_k_n_m*p + vehicle.secondary_k_n_m*s + vehicle.contact_k_n_m*(wheels.T@wheels)
    damping = vehicle.primary_c_ns_m*p + vehicle.secondary_c_ns_m*s
    return mass, damping, stiffness, primary, secondary


def axle_distances(vehicle):
    span = DIMENSIONS["wheelbase"]
    center_spacing = 2*vehicle.bogie_half_spacing_m
    return np.array([0, span, center_spacing, center_spacing+span])


def modal_properties(mass, damping, stiffness):
    scale = 1/np.sqrt(np.diag(mass))
    k = scale[:, None]*stiffness*scale
    c = scale[:, None]*damping*scale
    roots, vectors = np.linalg.eigh(k)
    frequency = np.sqrt(roots)
    state = np.block([[np.zeros_like(k), np.eye(len(k))], [-k, -c]])
    poles = sorted(np.linalg.eigvals(state), key=lambda p: (p.imag, p.real))
    return {"undamped_hz": (frequency/tau).tolist(),
            "mass_normalized_modes": (scale[:, None]*vectors).tolist(),
            "modal_projected_damping_ratio": (np.diag(vectors.T@c@vectors)/(2*frequency)).tolist(),
            "damped_poles_per_s": [[float(p.real), float(p.imag)] for p in poles]}


def experiment_config(case):
    return {"schema": "rail.vehicle-experiment.v1", "model": MODEL, "parameter_source": "teaching",
            "case": asdict(case)}


def read_experiment(data):
    if len(data) > 16_384:
        raise ValueError("工况配置须小于 16 KiB。")
    try:
        config = json.loads(data)
        if config["model"] != MODEL or config["schema"] != "rail.vehicle-experiment.v1" or config["parameter_source"] != "teaching":
            raise ValueError
        case = config["case"].copy()
        case["vehicle"] = VehicleParameters(**case["vehicle"])
        return VehicleCase(**case)
    except (KeyError, ValueError, TypeError, AttributeError, RecursionError, OverflowError):
        raise ValueError("无法读取该版本的教学工况配置。") from None


def simulate_vehicle(case, *, substeps=5):
    """Average-acceleration Newmark. Internal step is 1 / (sample rate × substeps)."""
    if type(substeps) is not int or not 5 <= substeps <= 40:
        raise ValueError("子步数应在 5–40 之间。")
    mass, damping, stiffness, primary, secondary = matrices(case.vehicle)
    dt = 1/(OUTPUT_HZ*substeps)
    count = case.duration_s*OUTPUT_HZ*substeps
    times = np.arange(count+1)*dt
    distances = axle_distances(case.vehicle)
    rail = track_height(case, times[:, None]*case.speed_m_s-distances)
    force = np.zeros((len(times), len(DOFS)))
    force[:, 6:] = case.vehicle.contact_k_n_m*rail
    q, dq, ddq = newmark_response(mass, damping, stiffness, force, dt, substeps)
    rail_output = rail[::substeps]
    vehicle = case.vehicle
    nominal_load = 9.80665*(vehicle.body_mass_kg/4 + vehicle.bogie_mass_kg/2 + vehicle.wheelset_mass_kg)
    linear_contact = nominal_load + vehicle.contact_k_n_m*(rail_output-q[:, 6:])
    config = {**experiment_config(case), "substeps": substeps}
    digest = sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**config, "evidence_id": f"simulation.{digest[:16]}", "configuration_sha256": digest,
            "source": "calculated", "sample_hz": OUTPUT_HZ, "time_s": times[::substeps].tolist(),
            "dofs": list(DOFS), "units_q": ["rad" if name.endswith("pitch") else "m" for name in DOFS],
            "q": q.tolist(), "dq": dq.tolist(), "ddq": ddq.tolist(), "rail_m": rail_output.tolist(),
            "min_linear_contact_n": float(np.min(linear_contact)), "contact_assumption_exceeded": bool(np.any(linear_contact <= 0)),
            "primary_m": (q@primary.T).tolist(), "secondary_m": (q@secondary.T).tolist(),
            "axle_delay_s": (distances/case.speed_m_s).tolist(),
            "modes": modal_properties(mass, damping, stiffness)}


def compare_vehicle_cases(case, *, speed_m_s, primary_factor=1, secondary_factor=1,
                          primary_damping_factor=1, secondary_damping_factor=1):
    factors = (primary_factor, secondary_factor, primary_damping_factor, secondary_damping_factor)
    if not all(_bounded(value, .25, 2) for value in factors):
        raise ValueError("工况对照的悬挂系数应为原值的 0.25–2 倍。")
    vehicle = case.vehicle
    changed = replace(vehicle, primary_k_n_m=vehicle.primary_k_n_m*primary_factor,
                      secondary_k_n_m=vehicle.secondary_k_n_m*secondary_factor,
                      primary_c_ns_m=vehicle.primary_c_ns_m*primary_damping_factor,
                      secondary_c_ns_m=vehicle.secondary_c_ns_m*secondary_damping_factor)
    return [simulate_vehicle(case), simulate_vehicle(replace(case, vehicle=changed, speed_m_s=speed_m_s))]


def summary(result):
    accel = np.asarray(result["ddq"])[:, 0]
    return {"evidence_id": result["evidence_id"], "body_rms_m_s2": float(np.sqrt(np.mean(accel**2))),
            "body_peak_m_s2": float(np.max(np.abs(accel))),
            "primary_peak_mm": float(1000*np.max(np.abs(result["primary_m"]))),
            "secondary_peak_mm": float(1000*np.max(np.abs(result["secondary_m"])))}


def local_sensitivity(case):
    """One-at-a-time central differences with ±5% perturbations."""
    vehicle = case.vehicle
    rows = []
    for field_name in ("primary_k_n_m", "primary_c_ns_m", "secondary_k_n_m", "secondary_c_ns_m"):
        value = getattr(vehicle, field_name)
        try:
            lower, upper = (replace(vehicle, **{field_name: value*factor}) for factor in (.95, 1.05))
        except ValueError:
            rows.append({"parameter": field_name, "status": "超出局部差分范围"})
            continue
        lower_result = summary(simulate_vehicle(replace(case, vehicle=lower)))
        upper_result = summary(simulate_vehicle(replace(case, vehicle=upper)))
        rows.append({"parameter": field_name, "status": "计算完成",
                     "body_rms_per_fraction_m_s2": (upper_result["body_rms_m_s2"]-lower_result["body_rms_m_s2"])/.10,
                     "primary_peak_per_fraction_mm": (upper_result["primary_peak_mm"]-lower_result["primary_peak_mm"])/.10,
                     "secondary_peak_per_fraction_mm": (upper_result["secondary_peak_mm"]-lower_result["secondary_peak_mm"])/.10})
    return rows


def bogie_playback(result, bogie, gain=1):
    """Viewer order is B (leading +X) then A (trailing -X)."""
    if bogie not in ("front", "rear") or gain not in (1, 2, 4):
        raise ValueError("未知转向架或动画倍率。")
    i = 0 if bogie == "front" else 1
    frame = 2 + 2*i
    wheel = 6 + 2*i
    q, ddq = np.asarray(result["q"]), np.asarray(result["ddq"])
    primary, secondary, rail = map(np.asarray, (result["primary_m"], result["secondary_m"], result["rail_m"]))
    channels = {"travel_m": primary[:, 2*i].tolist(), "front_travel_m": primary[:, 2*i].tolist(),
                "rear_travel_m": primary[:, 2*i+1].tolist(), "front_support_m": rail[:, 2*i].tolist(),
                "rear_support_m": rail[:, 2*i+1].tolist(), "accel_m_s2": ddq[:, frame].tolist(),
                "pitch_rad": q[:, frame+1].tolist(), "frame_m": q[:, frame].tolist(),
                "front_wheel_m": q[:, wheel].tolist(), "rear_wheel_m": q[:, wheel+1].tolist(),
                "secondary_m": secondary[:, i].tolist(),
                "wheel_rad": (np.asarray(result["time_s"])*result["case"]["speed_m_s"]/DIMENSIONS["tread_radius"]).tolist()}
    return {"vehicle_model": MODEL, "sample_hz": OUTPUT_HZ, "channels": channels,
            "parameters": {"evidence_id": result["evidence_id"], "bogie": bogie, "gain": gain},
            "wheelbase_m": DIMENSIONS["wheelbase"]}
