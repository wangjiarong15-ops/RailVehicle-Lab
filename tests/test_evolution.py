from dataclasses import replace
from math import hypot, isfinite, sqrt, tau

import pytest

from rail_vehicle.suspension import OUTPUT_HZ, SCENARIOS, SuspensionCase, simulate


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_all_cases_have_a_finite_si_sample_for_each_tick(scenario):
    case = SuspensionCase(scenario)
    result = simulate(case)
    channels = result["channels"]
    assert all(len(values) == 601 and all(map(isfinite, values)) for values in channels.values())
    assert channels["travel_m"] == pytest.approx([a-b for a,b in zip(channels["frame_m"], channels["support_m"])])
    if scenario in ("brake", "curve", "hunting", "level"):
        assert not any(channels["accel_m_s2"])
    if scenario == "brake":
        assert len(set(channels["wheel_rad"][400:])) == 1
        assert channels["wheel_rad"][399] < channels["wheel_rad"][400]


def test_base_excitation_matches_steady_state_transfer_function():
    case = SuspensionCase()
    result = simulate(case)
    omega = tau*case.speed_m_s/3
    k, c, m = case.stiffness, case.damping_ns_m, case.mass_kg
    amplitude = case.amplitude_m*hypot(k, c*omega)/hypot(k-m*omega**2, c*omega)
    last_cycle = result["channels"]["frame_m"][-OUTPUT_HZ:]
    assert sqrt(2*sum(v*v for v in last_cycle)/len(last_cycle)) == pytest.approx(amplitude, rel=.001)
    softer = simulate(replace(case, scenario="soft"))
    assert softer["natural_hz"] == pytest.approx(result["natural_hz"]/sqrt(2))
    assert softer["channels"]["support_m"] == result["channels"]["support_m"]


def test_axle_pass_delay_sign_and_zero_input():
    case = SuspensionCase("axle_bump", speed_m_s=5)
    result = simulate(case)
    c = result["channels"]
    assert result["axle_delay_s"] == pytest.approx(.52)
    assert c["front_support_m"][60] == pytest.approx(.004)
    assert c["rear_support_m"][112] == pytest.approx(.004)
    assert c["front_support_m"][:-52] == pytest.approx(c["rear_support_m"][52:])
    assert all(value == 0 for value in c["rear_support_m"][:102])
    assert max(c["pitch_rad"][51:61]) > 0
    for i, theta in enumerate(c["pitch_rad"]):
        front = c["front_travel_m"][i] + c["front_support_m"][i]
        rear = c["rear_travel_m"][i] + c["rear_support_m"][i]
        assert (front-rear)/result["wheelbase_m"] == pytest.approx(theta)
        assert (front+rear)/2 == pytest.approx(c["frame_m"][i])
    zero = simulate(replace(case, amplitude_m=0))["channels"]
    for name in ("front_travel_m", "rear_travel_m", "pitch_rad", "accel_m_s2"):
        assert not any(zero[name])


@pytest.mark.parametrize(("speed", "tolerance"), [(5, 1e-9), (6, 1e-7)])
def test_axle_pass_matches_independent_end_force_integration(speed, tolerance):
    from math import cos, sin
    integrate = pytest.importorskip("scipy.integrate")
    case = SuspensionCase("axle_bump", speed_m_s=speed)
    result = simulate(case)
    half = result["wheelbase_m"]/2
    def derivatives(t, state):
        z, v, theta, rate = state
        forces = []
        for x in (half, -half):
            distance = case.speed_m_s*(t-.5) - (half-x)
            h = dh = 0.
            if 0 < distance < 1:
                h = case.amplitude_m*(1-cos(tau*distance))/2
                dh = case.amplitude_m*tau*case.speed_m_s*sin(tau*distance)/2
            forces.append((case.stiffness_n_m*(h-z-x*theta) +
                           case.damping_ns_m*(dh-v-x*rate))/2)
        return v, sum(forces)/case.mass_kg, rate, half*(forces[0]-forces[1])/result["pitch_inertia_kg_m2"]
    times = [i/OUTPUT_HZ for i in range(601)]
    oracle = integrate.solve_ivp(derivatives, (0,6), (0,0,0,0), t_eval=times, method="DOP853",
                                 max_step=.02, atol=1e-12, rtol=1e-9)
    assert oracle.success
    for values, channel in ((oracle.y[0], "frame_m"), (oracle.y[2], "pitch_rad")):
        assert result["channels"][channel] == pytest.approx(values, abs=tolerance)


@pytest.mark.parametrize("params", [dict(mass_kg=0), dict(amplitude_m=float("nan")), dict(scenario="bad")])
def test_invalid_case(params):
    with pytest.raises(ValueError):
        SuspensionCase(**params)


def test_cursor_distinguishes_same_timestamp_and_respects_analysis_window():
    from rail_vehicle.replay import prepare_replay
    track = prepare_replay([dict(timestamp="2026-01-01T00:00:00Z", speed_kmh=20.,
                                 lateral_accel=a, vertical_accel=0.) for a in [0., 1.]])
    assert track.observation_at(0)["relative_seconds"] == track.observation_at(1)["relative_seconds"]
    assert track.observation_at(1)["lateral_accel"] == 1.
    for row in [0, True, 2]:
        with pytest.raises(ValueError):
            track.observation_at(row, first=1)


def test_fft_amplitude_kurtosis_and_axis_correlation():
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from datetime import datetime, timedelta, timezone
    from rail_vehicle.vibration import analyze_vibration
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [dict(timestamp=(start+timedelta(seconds=i/100)).isoformat(), speed_kmh=2.,
                 lateral_accel=float(1+.4*np.sin(tau*2*i/100)),
                 vertical_accel=float(-.8*np.sin(tau*2*i/100))) for i in range(1200)]
    result = analyze_vibration(rows)
    ch = result["channels"]["lateral_accel"]
    assert ch["fft_hz"][np.argmax(ch["fft_amplitude"])] == 2
    assert max(ch["fft_amplitude"]) == pytest.approx(.4)
    assert ch["kurtosis"] == pytest.approx(1.5)
    assert result["axis_correlation"] == pytest.approx(-1)
    rows[1]["timestamp"] = rows[0]["timestamp"]
    gated = analyze_vibration(rows)
    assert gated["axis_correlation"] is None
    assert gated["channels"]["lateral_accel"]["fft_hz"] == []
