"""Vertical wheel/rail component about a declared loaded equilibrium."""

from dataclasses import dataclass, asdict
from math import cos, sin, pi

import numpy as np

from rail_vehicle.wheel_rail import in_range, normal_force

MODEL = "rail.flexible-track-vertical.v1"
CHANNELS = {"wheel_z_m":"m", "rail_z_m":"m", "normal_n":"N", "wheel_accel_m_s2":"m/s²"}


@dataclass(frozen=True)
class TrackCase:
    wheel_mass_kg: float = 750.
    rail_mass_kg: float = 75.
    wheel_stiffness_n_m: float = 350_000.
    wheel_damping_ns_m: float = 4000.
    pad_stiffness_n_m: float = 40_000_000.
    pad_damping_ns_m: float = 16_000.
    static_normal_n: float = 55_000.
    hertz_n_m32: float = 8e10
    defect_depth_m: float = .0015
    support_factor: float = .80
    crossing_time_s: float = .12
    contact: str = "hertz"

    def __post_init__(self):
        spans = ((self.wheel_mass_kg,200,2500),(self.rail_mass_kg,20,350),
                 (self.wheel_stiffness_n_m,5e4,2e6),(self.wheel_damping_ns_m,0,5e4),
                 (self.pad_stiffness_n_m,2e6,1.5e8),(self.pad_damping_ns_m,0,8e4),
                 (self.static_normal_n,5e3,2e5),(self.hertz_n_m32,2e9,2e11),
                 (self.defect_depth_m,0,.008),(self.support_factor,.35,1.5),(self.crossing_time_s,.04,.35))
        if not all(in_range(*span) for span in spans) or self.contact not in ("hertz","linear"):
            raise ValueError("轮轨垂向组件参数超出实验范围。")

    @property
    def indentation(self):
        return (self.static_normal_n/self.hertz_n_m32)**(2/3)


def linear_matrices(case):
    tangent = 1.5*case.hertz_n_m32*case.indentation**.5
    return (np.diag([case.wheel_mass_kg, case.rail_mass_kg]),
            np.diag([case.wheel_damping_ns_m, case.pad_damping_ns_m]),
            np.array([[case.wheel_stiffness_n_m+tangent, -tangent],
                      [-tangent, case.pad_stiffness_n_m+tangent]]))


def pulse(time, duration):
    phase = (time-.15)/duration
    if not 0. < phase < 1.:
        return 0., 0.
    return .5*(1-cos(2*pi*phase)), pi*sin(2*pi*phase)/duration


def response(case: TrackCase, solver="DOP853", initial=(0., 0., 0., 0.)):
    from scipy.integrate import solve_ivp

    if solver not in ("DOP853","Radau") or len(initial) != 4 or not np.isfinite(initial).all():
        raise ValueError("轮轨组件求解输入无效。")
    indent = case.indentation
    tangent = 1.5*case.hertz_n_m32*indent**.5

    def components(time, state):
        wheel, rail, wheel_v, rail_v = state
        envelope, rate = pulse(time, case.crossing_time_s)
        base = -case.defect_depth_m*envelope
        base_v = -case.defect_depth_m*rate
        factor = 1+(case.support_factor-1)*envelope
        penetration = indent+rail-wheel
        normal = (float(normal_force(case.hertz_n_m32, penetration)) if case.contact=="hertz"
                  else case.static_normal_n+tangent*(rail-wheel))
        change = normal-case.static_normal_n
        pad = (factor-1)*case.static_normal_n-factor*case.pad_stiffness_n_m*(rail-base)-case.pad_damping_ns_m*(rail_v-base_v)
        wheel_a = (change-case.wheel_stiffness_n_m*wheel-case.wheel_damping_ns_m*wheel_v)/case.wheel_mass_kg
        rail_a = (pad-change)/case.rail_mass_kg
        return wheel_a, rail_a, normal, base

    def rhs(time, state):
        return (*state[2:], *components(time, state)[:2])

    times = np.linspace(0,.8,401)
    solved = solve_ivp(rhs, (0.,.8), initial, t_eval=times, method=solver, rtol=1e-8, atol=1e-10, max_step=.001)
    if not solved.success or len(solved.t) != len(times) or not np.isfinite(solved.y).all():
        raise ValueError("轮轨垂向组件求解失败。")
    values = np.asarray([components(t,q) for t,q in zip(times,solved.y.T,strict=True)])
    load = values[:,2]
    if case.contact=="linear" and np.any(load<=0):
        raise ValueError("线性接触预测发生失载，需改用单侧 Hertz 计算。")
    m,_,k = linear_matrices(case)
    modal_scale = np.sqrt(np.diag(m))
    modes = np.sqrt(np.linalg.eigvalsh(k/np.outer(modal_scale,modal_scale)))/(2*pi)
    return {"model":MODEL,"parameters":asdict(case),"solver":solver,"time_s":times.tolist(),
            "wheel_z_m":solved.y[0].tolist(),"rail_z_m":solved.y[1].tolist(),
            "wheel_accel_m_s2":values[:,0].tolist(),"normal_n":load.tolist(),
            "input_m":values[:,3].tolist(),"tangent_modes_hz":modes.tolist(),
            "separated_samples":int(np.count_nonzero(load<=0))}
