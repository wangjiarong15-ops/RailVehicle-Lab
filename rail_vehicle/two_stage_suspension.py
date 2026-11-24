"""Linear teaching model for vertical heave of the bogie frame and carbody."""

from dataclasses import asdict, dataclass, field
from math import hypot, isfinite, sqrt, tau

from rail_vehicle.suspension import DURATION, OUTPUT_HZ, RADIUS, SuspensionCase


@dataclass(frozen=True)
class TwoStageCase:
    primary: SuspensionCase = field(default_factory=SuspensionCase)
    body_mass_kg: float = 6000
    secondary_stiffness_n_m: float = 240000
    secondary_damping_ns_m: float = 12000

    def __post_init__(self):
        if self.primary.scenario != "rough":
            raise ValueError("两级悬挂仅支持周期支承输入")
        for value, low, high in ((self.body_mass_kg, 2000, 18000),
                                 (self.secondary_stiffness_n_m, 60000, 600000),
                                 (self.secondary_damping_ns_m, 1000, 40000)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
                raise ValueError("两级悬挂参数超出教学模型范围")

    def derivative(self, time, state):
        frame, frame_velocity, body, body_velocity = state
        base, base_velocity = self.primary.support(time)
        primary_force = (self.primary.stiffness_n_m * (base-frame) +
                         self.primary.damping_ns_m * (base_velocity-frame_velocity))
        secondary_force = (self.secondary_stiffness_n_m * (body-frame) +
                           self.secondary_damping_ns_m * (body_velocity-frame_velocity))
        return (frame_velocity, (primary_force+secondary_force)/self.primary.mass_kg,
                body_velocity, -secondary_force/self.body_mass_kg)

    def modes(self):
        frame_mass, body_mass = self.primary.mass_kg, self.body_mass_kg
        primary_k, secondary_k = self.primary.stiffness_n_m, self.secondary_stiffness_n_m
        frame_diag = (primary_k+secondary_k)/frame_mass
        body_diag = secondary_k/body_mass
        coupling = -secondary_k/sqrt(frame_mass*body_mass)
        upper = (frame_diag+body_diag+hypot(frame_diag-body_diag, 2*coupling))/2
        lower = primary_k*secondary_k/(frame_mass*body_mass*upper)
        result = []
        for eigenvalue in (lower, upper):
            # Undamped, mass-normalized mode. The damping matrix can also couple the two modes.
            frame, body = coupling, eigenvalue-frame_diag
            norm = hypot(frame, body)
            modal_damping = (self.primary.damping_ns_m*(frame/sqrt(frame_mass))**2 +
                             self.secondary_damping_ns_m*(body/sqrt(body_mass)-frame/sqrt(frame_mass))**2)/norm**2
            result.append({"natural_hz": sqrt(eigenvalue)/tau,
                           "modal_damping_ratio": modal_damping/(2*sqrt(eigenvalue))})
        return result

    def response(self, frequency_hz):
        """Complex steady-state displacement per metre of sinusoidal support input."""
        if not isfinite(frequency_hz) or not 0 <= frequency_hz <= 100:
            raise ValueError("频率须在 0 至 100 Hz")
        omega = tau*frequency_hz
        p = complex(self.primary.stiffness_n_m, omega*self.primary.damping_ns_m)
        s = complex(self.secondary_stiffness_n_m, omega*self.secondary_damping_ns_m)
        a = p+s-self.primary.mass_kg*omega**2
        d = s-self.body_mass_kg*omega**2
        determinant = a*d-s*s
        return p*d/determinant, p*s/determinant


def _advance(case, t, state, dt):
    def shifted(derivative, step):
        return tuple(value+step*slope for value, slope in zip(state, derivative, strict=True))

    a = case.derivative(t, state)
    b = case.derivative(t+dt/2, shifted(a, dt/2))
    c = case.derivative(t+dt/2, shifted(b, dt/2))
    d = case.derivative(t+dt, shifted(c, dt))
    return tuple(value+dt*(i+2*j+2*k+l)/6 for value, i, j, k, l in zip(state, a, b, c, d, strict=True))


def simulate_two_stage(case: TwoStageCase):
    state = (0., 0., 0., 0.)
    dt = .001
    names = ("support_m", "frame_m", "travel_m", "accel_m_s2", "wheel_rad", "lateral_m", "yaw_rad",
             "body_m", "secondary_travel_m", "body_accel_m_s2")
    channels = {name: [] for name in names}
    last_step = DURATION*1000
    for i in range(last_step+1):
        time = i*dt
        if i % (1000//OUTPUT_HZ) == 0:
            frame, _, body, _ = state
            support, _ = case.primary.support(time)
            _, frame_accel, _, body_accel = case.derivative(time, state)
            sample = (support, frame, frame-support, frame_accel, case.primary.speed_m_s*time/RADIUS, 0., 0.,
                      body, body-frame, body_accel)
            for name, value in zip(names, sample, strict=True):
                channels[name].append(value)
        if i < last_step:
            state = _advance(case, time, state, dt)
    return dict(version=1, source="calculated", scenario="two_stage", sample_hz=OUTPUT_HZ,
                parameters=asdict(case), channels=channels, modes=case.modes())
