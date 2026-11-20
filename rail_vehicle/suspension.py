"""Teaching models with prescribed vertical support displacement, in SI units."""
from dataclasses import asdict, dataclass
from math import cos, isfinite, pi, sin, sqrt, tau

from rail_vehicle.bogie_model_spec import DIMENSIONS


SCENARIOS = {
    "level": "正常运行", "rough": "周期轨道不平顺", "flat": "车轮扁疤等效激励",
    "soft": "悬挂刚度降低", "hunting": "蛇行运动示意", "curve": "曲线通过示意", "brake": "匀减速制动",
    "axle_bump": "双轴先后通过局部不平顺",
}
DURATION = 6
OUTPUT_HZ = 100
RADIUS = DIMENSIONS["tread_radius"]
BUMP_START_S = .5
BUMP_LENGTH_M = 1.0


def _bump_input(elapsed, speed_m_s, amplitude_m):
    if not 0 < elapsed < BUMP_LENGTH_M/speed_m_s:
        return 0., 0.
    omega = tau*speed_m_s/BUMP_LENGTH_M
    return (.5*amplitude_m*(1-cos(omega*elapsed)), .5*amplitude_m*omega*sin(omega*elapsed))


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

    def axle_bump_inputs(self, t):
        """Prescribed (height, velocity) at the leading B and trailing A supports."""
        elapsed = t-BUMP_START_S
        delay = DIMENSIONS["wheelbase"]/self.speed_m_s
        return (_bump_input(elapsed, self.speed_m_s, self.amplitude_m),
                _bump_input(elapsed-delay, self.speed_m_s, self.amplitude_m))

    def support(self, t):
        if self.scenario == "axle_bump":
            (front, vf), (rear, vr) = self.axle_bump_inputs(t)
            return (front+rear)/2, (vf+vr)/2
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


def _rk4_step(acceleration, t, z, velocity, dt, a1):
    v2 = velocity + dt*a1/2
    a2 = acceleration(t+dt/2, z+dt*velocity/2, v2)
    v3 = velocity + dt*a2/2
    a3 = acceleration(t+dt/2, z+dt*v2/2, v3)
    v4 = velocity + dt*a3
    a4 = acceleration(t+dt, z+dt*v3, v4)
    return z + dt*(velocity+2*v2+2*v3+v4)/6, velocity + dt*(a1+2*a2+2*a3+a4)/6


def _simulate_axle_pass(case):
    # B leads at positive X. Each end carries half the total stiffness and damping.
    span = DIMENSIONS["wheelbase"]
    half = span/2
    delay = span/case.speed_m_s
    radius_of_gyration = 1.0  # assumed, metres
    inertia = case.mass_kg * radius_of_gyration**2
    def pitch_accel(t, theta, rate):
        (front, vf), (rear, vr) = case.axle_bump_inputs(t)
        return half**2/inertia * (case.stiffness*((front-rear)/span-theta) +
                                  case.damping_ns_m*((vf-vr)/span-rate))

    channels = {key: [] for key in (
        "support_m", "frame_m", "travel_m", "accel_m_s2", "wheel_rad", "lateral_m", "yaw_rad",
        "front_support_m", "rear_support_m", "front_travel_m", "rear_travel_m", "pitch_rad")}
    dt = .001
    z = velocity = theta = pitch_rate = 0.
    for i in range(DURATION*1000+1):
        t = i*dt
        az = case.acceleration(t, z, velocity)
        angular_accel = pitch_accel(t, theta, pitch_rate)
        if i % 10 == 0:
            (front, _), (rear, _) = case.axle_bump_inputs(t)
            mean_support = (front+rear)/2
            sample = dict(support_m=mean_support, frame_m=z, travel_m=z-mean_support, accel_m_s2=az,
                          wheel_rad=case.speed_m_s*t/RADIUS, lateral_m=0., yaw_rad=0.,
                          front_support_m=front, rear_support_m=rear, pitch_rad=theta,
                          front_travel_m=z+half*theta-front, rear_travel_m=z-half*theta-rear)
            for key, value in sample.items():
                channels[key].append(value)
        if i < DURATION*1000:
            z, velocity = _rk4_step(case.acceleration, t, z, velocity, dt, az)
            theta, pitch_rate = _rk4_step(pitch_accel, t, theta, pitch_rate, dt, angular_accel)
    return dict(version=2, source="calculated", scenario=case.scenario, parameters=asdict(case),
                sample_hz=OUTPUT_HZ, wheelbase_m=span, axle_delay_s=delay, bump_start_s=BUMP_START_S,
                bump_length_m=BUMP_LENGTH_M, pitch_inertia_kg_m2=inertia, channels=channels,
                natural_hz=sqrt(case.stiffness/case.mass_kg)/tau,
                pitch_natural_hz=sqrt(case.stiffness*half**2/inertia)/tau)


def simulate(case: SuspensionCase):
    """Versioned, JSON-compatible result. RK4 at 1 kHz; publish every tenth step."""
    if case.scenario == "axle_bump":
        return _simulate_axle_pass(case)
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
        z, velocity = _rk4_step(case.acceleration, t, z, velocity, dt, a1)
    return dict(version=1, source="calculated", scenario=case.scenario, parameters=asdict(case), sample_hz=OUTPUT_HZ,
                natural_hz=sqrt(case.stiffness/case.mass_kg)/tau, channels=channels)
