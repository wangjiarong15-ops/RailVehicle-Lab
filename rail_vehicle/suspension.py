"""One vertical sprung mass with prescribed support displacement. SI units."""
from dataclasses import asdict, dataclass
from math import cos, isfinite, pi, sin, sqrt, tau

from rail_vehicle.bogie_model_spec import DIMENSIONS


SCENARIOS = {
    "level": "正常运行", "rough": "周期轨道不平顺", "flat": "车轮扁疤等效激励",
    "soft": "悬挂刚度降低", "hunting": "蛇行运动示意", "curve": "曲线通过示意", "brake": "匀减速制动",
}
DURATION = 6
OUTPUT_HZ = 100
RADIUS = DIMENSIONS["tread_radius"]


@dataclass(frozen=True)
class SuspensionCase:
    scenario: str = "rough"
    mass_kg: float = 1200
    stiffness_n_m: float = 450000
    damping_ns_m: float = 10000
    speed_m_s: float = 6
    amplitude_m: float = .004

    def __post_init__(self):
        if self.scenario not in SCENARIOS:
            raise ValueError("未知计算工况")
        for value, low, high in ((self.mass_kg, 500, 2500), (self.stiffness_n_m, 100000, 1000000),
                                 (self.damping_ns_m, 1000, 40000), (self.speed_m_s, 1, 10),
                                 (self.amplitude_m, 0, .008)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
                raise ValueError("计算参数超出示例模型支持范围")

    @property
    def stiffness(self):
        return self.stiffness_n_m * (.5 if self.scenario == "soft" else 1)

    def support(self, t):
        if self.scenario in ("rough", "soft"):
            omega = tau * self.speed_m_s / 3  # A three metre sinusoidal wavelength.
            return self.amplitude_m * sin(omega*t), self.amplitude_m * omega * cos(omega*t)
        if self.scenario == "flat":
            # One smooth depression per revolution, occupying one fifth of the circumference.
            rotation_hz = self.speed_m_s / (tau*RADIUS)
            fraction = (t*rotation_hz) % 1
            if fraction < .2:
                angle = tau*fraction/.2
                return (-.5*self.amplitude_m*(1-cos(angle)),
                        -.5*self.amplitude_m*sin(angle)*tau*rotation_hz/.2)
        return 0., 0.

    def acceleration(self, t, z, velocity):
        base, base_velocity = self.support(t)
        return (self.stiffness*(base-z) + self.damping_ns_m*(base_velocity-velocity))/self.mass_kg


def simulate(case: SuspensionCase):
    """Versioned, JSON-compatible result. RK4 at 1 kHz; publish every tenth step."""
    dt = .001
    z = velocity = 0.
    channels = {name: [] for name in ("support_m", "frame_m", "travel_m", "accel_m_s2",
                                       "wheel_rad", "lateral_m", "yaw_rad")}
    for i in range(DURATION*1000+1):
        t = i*dt
        a1 = case.acceleration(t, z, velocity)
        if i % 10 == 0:
            base, _ = case.support(t)
            distance = case.speed_m_s*t
            lateral = yaw = 0.
            if case.scenario == "brake":
                # Stop after four seconds under a prescribed constant deceleration.
                elapsed = min(t, 4)
                distance = case.speed_m_s*(elapsed - elapsed**2/8)
            elif case.scenario == "hunting":
                lateral = .010*sin(tau*t)
                yaw = .004*sin(tau*t + pi/2)
            elif case.scenario == "curve":
                # Local tangent view, prescribed sway on transition into a constant-radius curve.
                lateral = .008*(1-cos(pi*min(t/2, 1)))/2
            for key, value in zip(channels, (base, z, z-base, a1, distance/RADIUS, lateral, yaw)):
                channels[key].append(value)
        if i == DURATION*1000:
            break
        v2 = velocity + dt*a1/2
        a2 = case.acceleration(t+dt/2, z+dt*velocity/2, v2)
        v3 = velocity + dt*a2/2
        a3 = case.acceleration(t+dt/2, z+dt*v2/2, v3)
        v4 = velocity + dt*a3
        a4 = case.acceleration(t+dt, z+dt*v3, v4)
        z += dt*(velocity+2*v2+2*v3+v4)/6
        velocity += dt*(a1+2*a2+2*a3+a4)/6
    return dict(version=1, source="calculated", scenario=case.scenario, parameters=asdict(case), sample_hz=OUTPUT_HZ,
                natural_hz=sqrt(case.stiffness/case.mass_kg)/tau, channels=channels)
