from dataclasses import replace
from math import sqrt, tau

import pytest

from rail_vehicle.suspension import SuspensionCase
from rail_vehicle.two_stage_suspension import TwoStageCase, simulate_two_stage


def test_modes_match_closed_form_for_equal_masses_and_springs():
    primary = SuspensionCase(mass_kg=2000, stiffness_n_m=240000)
    case = TwoStageCase(primary, body_mass_kg=2000, secondary_stiffness_n_m=240000)
    # K/m = 120 * [[2, -1], [-1, 1]]. Roots are 120*(3 ± sqrt(5))/2.
    hz = [sqrt(60*(3-sqrt(5)))/tau, sqrt(60*(3+sqrt(5)))/tau]
    modes = case.modes()
    assert [row["natural_hz"] for row in modes] == pytest.approx(hz)
    assert all(0 < row["modal_damping_ratio"] < 1 for row in modes)
    assert case.response(0) == pytest.approx((1+0j, 1+0j))
    assert replace(case, secondary_stiffness_n_m=288000).modes()[0]["natural_hz"] > hz[0]


def test_no_input_is_zero_and_two_stage_samples_reconcile():
    quiet = simulate_two_stage(TwoStageCase(SuspensionCase(amplitude_m=0)))
    assert quiet["sample_hz"] == 100
    for key, values in quiet["channels"].items():
        assert len(values) == 601
        if key != "wheel_rad":
            assert values == [0.] * 601
    case = TwoStageCase()
    c = simulate_two_stage(case)["channels"]
    assert c["travel_m"] == pytest.approx([frame-base for frame, base in zip(c["frame_m"], c["support_m"])])
    assert c["secondary_travel_m"] == pytest.approx([body-frame for body, frame in zip(c["body_m"], c["frame_m"])])
    assert max(map(abs, c["secondary_travel_m"])) > 0


def test_integration_matches_independent_adaptive_solution():
    scipy = pytest.importorskip("scipy.integrate")
    case = TwoStageCase()

    def reference(t, states):
        z, b, zdot, bdot = states
        y, dy = case.primary.support(t)
        return [zdot, bdot,
                (-690000*z + 240000*b - 22000*zdot + 12000*bdot + 450000*y + 10000*dy) / 1200,
                (240000*z - 240000*b + 12000*zdot - 12000*bdot) / 6000]

    times = [i/100 for i in range(601)]
    baseline = scipy.solve_ivp(reference, [0, 6], [0]*4, t_eval=times, rtol=1e-11, atol=1e-13, method="DOP853")
    assert baseline.success
    c = simulate_two_stage(case)["channels"]
    for field, values in zip(("frame_m", "body_m"), baseline.y[:2], strict=True):
        assert c[field] == pytest.approx(values, abs=3e-10)


def test_rejects_wrong_scenario_and_invalid_mass():
    with pytest.raises(ValueError, match="周期"):
        TwoStageCase(SuspensionCase("brake"))
    with pytest.raises(ValueError, match="范围"):
        TwoStageCase(body_mass_kg=float("nan"))
