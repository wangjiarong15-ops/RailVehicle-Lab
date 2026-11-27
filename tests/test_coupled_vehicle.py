from dataclasses import replace
import json

import numpy as np
import pytest

from rail_vehicle.coupled_vehicle import (CoupledCase, case_config, force_at, frequency_response, matrices,
                                          read_case, simulate, spatial_input, summary)
from rail_vehicle.vehicle_dynamics import axle_distances, matrices as vertical_matrices


def test_matrices_zero_input_and_static_translation():
    case = CoupledCase(track="level", duration_s=1)
    m, c, k, _, _ = matrices(case)
    for matrix, legacy in zip((m, c, k), vertical_matrices(case.vehicle)[:3], strict=True):
        np.testing.assert_allclose(matrix, matrix.T)
        np.testing.assert_array_equal(matrix[:10, :10], legacy)
        assert not np.any(matrix[:10, 10:])
    assert np.min(np.linalg.eigvalsh(m)) > 0
    assert np.min(np.linalg.eigvalsh(k)) > 0
    # Dampers act on relative velocities, so C can have a nullspace.
    assert np.min(np.linalg.eigvalsh(c)) >= -1e-12*np.linalg.norm(c, 2)
    system = np.block([[np.zeros_like(m), np.eye(len(m))], [-np.linalg.solve(m, k), -np.linalg.solve(m, c)]])
    assert np.max(np.linalg.eigvals(system).real) < 0
    response = simulate(case)
    assert not np.any(response["q"])
    assert summary(response)["y_displacement_rms_ratio"] is None
    assert summary(response)["contact_assumption_exceeded"] is False
    dc = frequency_response(case, [0])[0]
    assert dc["body_y_over_input"] == pytest.approx(1)
    assert dc["body_roll_rad_per_m"] < 1e-12


def test_dop853_reference_convergence_equilibrium_and_decay():
    integration = pytest.importorskip("scipy.integrate")
    case = CoupledCase(duration_s=2)
    m, c, k, rows, _ = matrices(case)
    n = len(m)
    inverse_mass = 1/np.diag(m)

    def rhs(t, state):
        q, dq = state[:n], state[n:]
        force, _, _ = force_at(case, t, rows)
        return np.r_[dq, inverse_mass*(force-c@dq-k@q)]

    coarse = simulate(case, substeps=5, include_modes=False)
    fine = simulate(case, substeps=10, include_modes=False)
    t = fine["time_s"]
    independent = integration.solve_ivp(rhs, (0, case.duration_s), np.zeros(2*n), method="DOP853",
                                        t_eval=t, max_step=.004, atol=2e-12, rtol=2e-10)
    assert independent.success
    error = np.max(np.abs(np.array(coarse["q"])-independent.y[:n].T))
    refined_error = np.max(np.abs(np.array(fine["q"])-independent.y[:n].T))
    assert error < 1.5e-5
    assert refined_error < .4*error
    q, dq, ddq = (np.asarray(fine[name]) for name in ("q", "dq", "ddq"))
    forces = force_at(case, t, rows)[0]
    np.testing.assert_allclose(ddq@m + dq@c + q@k, forces, atol=1e-8)
    energy = np.sum(q*(q@k) + dq*(dq@m), axis=1)/2
    free = np.asarray(t) > (axle_distances(case.vehicle)[-1]+case.length_m)/case.speed_m_s
    assert energy[free][-1] < energy[free][0]
    assert np.max(np.diff(energy[free])) < 1e-8


def test_spatial_delay_seed_and_configuration():
    case = CoupledCase(duration_s=2)
    t = (axle_distances(case.vehicle)+case.length_m/2)/case.speed_m_s
    forces, y, z = force_at(case, t, matrices(case)[3])
    np.testing.assert_allclose(np.diag(y), case.lateral_m)
    np.testing.assert_allclose(np.diag(z), case.vertical_m)
    assert not np.allclose(y[:, 0], y[:, 3])
    assert np.isfinite(forces).all()
    x = [0, 6, 8, 20]
    seeded = replace(case, track="random")
    np.testing.assert_array_equal(spatial_input(seeded, x), spatial_input(seeded, x))
    assert not np.allclose(spatial_input(seeded, x), spatial_input(replace(seeded, seed=7), x))
    custom = replace(case, track="custom", profile=((0, 0, 0), (2, 1, -.5), (4, 0, 0)))
    assert read_case(json.dumps(case_config(custom))) == custom
    y, dy, z = spatial_input(custom, [-1, 1, 3, 4])
    np.testing.assert_allclose(y, [0, .004, .004, 0])
    np.testing.assert_allclose(dy, [0, .08, -.08, 0])
    np.testing.assert_allclose(z, [0, -.00075, -.00075, 0])
    with pytest.raises(ValueError):
        replace(custom, profile=((0, 0, 0), (.1, 0, 0)))
    with pytest.raises(ValueError):
        replace(case, speed_m_s=True)
