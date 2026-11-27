from dataclasses import replace
import json

import numpy as np
import pytest

from rail_vehicle.copilot.archive import export_chat, read_chat, read_engineering_context
from rail_vehicle.copilot.context import read_inspection_state
from rail_vehicle.copilot.conversation import Conversation, Turn
from rail_vehicle.copilot.planning import call, local_plan
from rail_vehicle.copilot.tools import Work, execute
from rail_vehicle.lateral_dynamics import (HALF_SPAN_M, LateralCase, case_config, excitation, lateral_playback,
                                          matrices, read_case, simulate_lateral)


def test_zero_input_and_equilibrium_energy_and_geometry():
    case = LateralCase(track="level", duration_s=1)
    result = simulate_lateral(case)
    assert np.count_nonzero(result["q"]) == np.count_nonzero(result["ddq"]) == 0
    mass, damping, stiffness, arm = matrices(case)
    for matrix in (mass, damping, stiffness):
        assert np.allclose(matrix, matrix.T)
        assert np.linalg.eigvalsh(matrix).min() > 0
    assert arm.tolist() == [1, case.com_height_m]
    assert all(real < 0 for real, _ in result["modes"]["damped_poles_per_s"])
    shifted = simulate_lateral(replace(case, track="bump", duration_s=2))
    q, dq, ddq = (np.asarray(shifted[key]) for key in ("q", "dq", "ddq"))
    base, velocity = excitation(replace(case, track="bump", duration_s=2), shifted["time_s"])
    residual = ddq@mass + dq@damping + q@stiffness - (case.lateral_k_n_m*base + case.lateral_c_ns_m*velocity)[:, None]*arm
    assert np.max(np.abs(residual)) < 1e-8
    energy = (np.sum(dq*(dq@mass), axis=1) + np.sum(q*(q@stiffness), axis=1))/2
    assert np.max(np.diff(energy[101:])) < 1e-9
    np.testing.assert_allclose(np.asarray(shifted["air_spring_vertical_m"])[:, 1], HALF_SPAN_M*q[:, 1])
    np.testing.assert_allclose(np.asarray(shifted["air_spring_vertical_m"])[:, 0], -HALF_SPAN_M*q[:, 1])


def test_independent_solver_and_timestep_convergence():
    scipy = pytest.importorskip("scipy.integrate")
    case = LateralCase(track="bump", duration_s=2)
    mass, damping, stiffness, arm = matrices(case)

    def derivative(t, state):
        base, velocity = excitation(case, t)
        q, dq = state[:2], state[2:]
        force = arm*(case.lateral_k_n_m*base+case.lateral_c_ns_m*velocity)
        return np.r_[dq, np.linalg.solve(mass, force-damping@dq-stiffness@q)]

    coarse = simulate_lateral(case, substeps=5)
    fine = simulate_lateral(case, substeps=10)
    reference = scipy.solve_ivp(derivative, (0, case.duration_s), np.zeros(4), t_eval=coarse["time_s"],
                                method="DOP853", atol=1e-12, rtol=1e-11, max_step=.007)
    expected = reference.y[:2].T
    assert reference.success
    error = np.max(np.abs(np.asarray(coarse["q"])-expected), axis=0)
    refined_error = np.max(np.abs(np.asarray(fine["q"])-expected), axis=0)
    assert np.all(error < [2e-7, 2e-7])
    assert np.all(refined_error < error/3)


def test_serialized_copilot_case_and_follow_up_reuse_original_evidence():
    base = LateralCase(track="sine", duration_s=8, speed_m_s=16)
    plan = local_plan("计算侧滚，抗侧滚刚度提高20%")
    assert plan == [call("simulate_lateral", speed=None, anti_roll_factor=1.2)]
    out = list(execute(Work(None, lateral_case=base), plan))[-1]["result"]
    before, after = out.provenance["simulation_comparison"]
    assert before["evidence_id"] == simulate_lateral(base)["evidence_id"]
    assert after["case"]["anti_roll_k_nm_rad"] == 120000
    case = read_case(json.dumps(out.provenance["replayable_case"]))
    assert case_config(case) == out.provenance["replayable_case"]
    playback = lateral_playback(simulate_lateral(case), 2)
    assert playback["channels"]["roll_rad"] == out.scene["simulation"]["channels"]["roll_rad"]
    assert playback["parameters"]["evidence_id"] == after["evidence_id"]
    assert local_plan("再把速度设为 25 m/s", plan) == [call("simulate_lateral", speed=25, anti_roll_factor=1.2)]
    assert local_plan("再用整车仿真比较速度 25 m/s", plan)[0]["tool"] == "simulate_vehicle"
    assert list(execute(Work(None, previous_scene=out.scene),
                        [call("transport", action="pause", seconds=7)]))[-1]["result"].scene["transport"]["seconds"] == 7
    with pytest.raises(ValueError, match="超过当前工况"):
        list(execute(Work(None, previous_scene=out.scene), [call("transport", action="pause", seconds=9)]))


@pytest.mark.parametrize("change", [{"speed_m_s": True}, {"amplitude_m": float("nan")}, {"duration_s": 16},
                                    {"anti_roll_k_nm_rad": -1}])
def test_invalid_inputs(change):
    with pytest.raises(ValueError):
        LateralCase(**change)


def test_explicit_view_capture_and_archived_context_are_validated():
    state = dict(schema="rail.bogie-inspection.v1", part_id="coil_a_l", view="侧视图", surface="selected",
                 isolated=None, cut_axis="x", cut_m=.15)
    assert read_inspection_state(state) == state
    assert read_inspection_state({**state, "part_id": "unknown-part"}) is None
    assert read_inspection_state({**state, "cut_m": float("inf")}) is None
    assert read_inspection_state({**state, "arbitrary_command": "untrusted"}) is None
    plan = local_plan("查看当前三维检查状态")
    assert plan == [call("current_inspection")]
    result = list(execute(Work(None, inspection_state=state), plan))[-1]["result"]
    assert result.provenance == {"inspection": state}
    assert "A 轴 左侧 一系螺旋弹簧" in result.text and "X = 0.15 m" in result.text
    convo = Conversation(("unbound",))
    convo.turns.append(Turn("查看当前三维检查状态", "离线工具", status="complete"))
    case = LateralCase()
    exported = export_chat(convo, lateral_case=case, inspection_state=state)
    assert read_chat(exported) == [("查看当前三维检查状态", [])]
    assert read_engineering_context(exported) == {"cp_lateral_case": case, "cp_inspection_state": state}
    damaged = json.loads(exported)
    damaged["inspection_state"]["part_id"] = "unknown-part"
    with pytest.raises(ValueError, match="不符合当前模型契约"):
        read_engineering_context(json.dumps(damaged))
