from dataclasses import replace
import json

import numpy as np
import pytest

from rail_vehicle.vehicle_dynamics import (
    TRACKS, VehicleCase, VehicleParameters, axle_distances, bogie_playback, compare_vehicle_cases,
    experiment_config, local_sensitivity, matrices, read_experiment, simulate_vehicle, summary,
)
from rail_vehicle.verification_residuals import compare_vehicle_reference


def test_matrices_rigid_translation_and_modes():
    case = VehicleCase(duration_s=1)
    mass, damping, stiffness, primary, secondary = matrices(case.vehicle)
    shift = np.array([1, 0, 1, 0, 1, 0, 1, 1, 1, 1])
    for matrix in (mass, damping, stiffness):
        np.testing.assert_array_equal(matrix, matrix.T)
    assert np.min(np.linalg.eigvalsh(damping)) >= -1e-8
    assert np.min(np.linalg.eigvalsh(stiffness)) > 0
    assert np.all(np.diag(mass) > 0)
    np.testing.assert_array_equal(primary @ shift, 0)
    np.testing.assert_array_equal(secondary @ shift, 0)
    static = np.linalg.solve(stiffness, np.r_[np.zeros(6), np.full(4, case.vehicle.contact_k_n_m)])
    np.testing.assert_allclose(static, shift, atol=1e-12)
    modes = simulate_vehicle(case)["modes"]
    basis = np.asarray(modes["mass_normalized_modes"])
    np.testing.assert_allclose(basis.T @ mass @ basis, np.eye(10), atol=1e-13)
    assert all(real < 0 for real, _ in modes["damped_poles_per_s"])
    assert modes["undamped_hz"] == sorted(modes["undamped_hz"])


@pytest.mark.parametrize("track", TRACKS)
def test_track_delay_zero_input_and_config_round_trip(track):
    case = VehicleCase(track=track, speed_m_s=13, duration_s=1)
    config = json.dumps(experiment_config(case)).encode()
    assert read_experiment(config) == case
    with pytest.raises(ValueError, match="配置"):
        read_experiment(config.replace(b"teaching", b"verified"))
    delay = axle_distances(case.vehicle)/case.speed_m_s
    result = simulate_vehicle(case)
    assert result["axle_delay_s"] == pytest.approx(delay)
    rail = np.asarray(result["rail_m"])
    for axle, start in enumerate(delay):
        np.testing.assert_array_equal(rail[np.asarray(result["time_s"]) <= start, axle], 0)
    assert np.max(np.abs(rail)) > 0
    quiet = simulate_vehicle(replace(case, amplitude_m=0))
    np.testing.assert_array_equal(quiet["q"], 0)
    np.testing.assert_array_equal(quiet["ddq"], 0)
    assert quiet["min_linear_contact_n"] > 0 and not quiet["contact_assumption_exceeded"]
    if track == "random":
        assert simulate_vehicle(case)["rail_m"] == result["rail_m"]
        assert simulate_vehicle(replace(case, seed=43))["rail_m"] != result["rail_m"]


def test_independent_dop853_and_convergence():
    solver = pytest.importorskip("scipy.integrate")
    case = VehicleCase(track="bump", speed_m_s=23, duration_s=2)
    model = simulate_vehicle(case)
    mass, damping, stiffness, _, _ = matrices(case.vehicle)
    offsets = axle_distances(case.vehicle)

    def derivative(t, state):
        q, dq = state[:10], state[10:]
        x = (t*23-offsets)/8
        heights = np.where((x > 0) & (x < 1), .0015*(1-np.cos(2*np.pi*x)), 0)
        force = np.r_[np.zeros(6), heights*90_000_000]
        return np.r_[dq, (force-damping@dq-stiffness@q)/np.diag(mass)]

    exact = solver.solve_ivp(derivative, (0, 2), np.zeros(20), t_eval=model["time_s"],
                            method="DOP853", rtol=1e-11, atol=1e-13, max_step=.004)
    assert exact.success
    errors = [np.max(np.abs(np.asarray(simulate_vehicle(case, substeps=n)["q"])-exact.y[:10].T))
              for n in (5, 10, 20)]
    assert errors[2] < errors[1] < errors[0]
    assert errors[2] < errors[0]/8
    assert errors[1] < 5e-6
    result = simulate_vehicle(replace(case, duration_s=6))
    q, dq = np.asarray(result["q"]), np.asarray(result["dq"])
    energy = .5*np.sum((dq@mass)*dq + (q@stiffness)*q, axis=1)
    after = np.asarray(result["time_s"]) > (offsets[-1]+8)/23
    assert np.all(np.diff(energy[after]) < 1e-10)


def test_comparison_and_bogie_mapping():
    case = VehicleCase(duration_s=2)
    base, changed = compare_vehicle_cases(case, speed_m_s=25, secondary_factor=1.2)
    assert summary(base)["evidence_id"] != summary(changed)["evidence_id"]
    assert changed["case"]["vehicle"]["secondary_k_n_m"] == case.vehicle.secondary_k_n_m*1.2
    assert changed["axle_delay_s"][3] < base["axle_delay_s"][3]
    payload = bogie_playback(changed, "rear", 4)
    assert payload["parameters"]["gain"] == 4
    np.testing.assert_array_equal(payload["channels"]["front_wheel_m"], np.asarray(changed["q"])[:, 8])
    np.testing.assert_array_equal(payload["channels"]["pitch_rad"], np.asarray(changed["q"])[:, 5])
    with pytest.raises(ValueError):
        VehicleParameters(body_mass_kg=float("nan"))


def test_synthetic_residual_and_external_source_declaration():
    result = simulate_vehicle(VehicleCase(amplitude_m=0, duration_s=1))
    records = {"reference_record": "授权流程测试用虚构数据", "alignment_record": "+Z，静平衡零时刻"}
    match = compare_vehicle_reference(result, "front_bogie_z",
                                      b"model_time_s,reference_axis_m_s2\n0,1\n.01,-1\n.02,1\n",
                                      reference_kind="synthetic", **records)
    assert match["metrics"]["rmse_m_s2"] == match["metrics"]["mae_m_s2"] == 1
    assert match["metrics"]["pearson_r"] is None
    assert match["evidence_id"] == result["evidence_id"] and match["independent_measurement"] == "pending"
    with pytest.raises(ValueError, match="方向"):
        compare_vehicle_reference(result, "body_pitch", b"", reference_kind="public_declared", **records)


def test_sensitivity_preserves_zero_and_reports_parameter_boundary():
    case = VehicleCase(amplitude_m=0, duration_s=1,
                       vehicle=VehicleParameters(primary_k_n_m=200_000))
    rows = local_sensitivity(case)
    assert rows[0]["status"] == "超出局部差分范围"
    assert all(row["body_rms_per_fraction_m_s2"] == 0 for row in rows[1:])
