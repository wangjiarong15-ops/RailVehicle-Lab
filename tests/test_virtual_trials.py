from dataclasses import replace
from threading import Event

import pytest

from rail_vehicle.copilot.archive import export_chat, read_engineering_context
from rail_vehicle.copilot.conversation import Conversation, Job, Turn
from rail_vehicle.copilot.planning import local_plan
from rail_vehicle.copilot.tools import Work, execute, validate_plan
from rail_vehicle.coupled_vehicle import CoupledCase, playback
from rail_vehicle.db import init_db, get_connection
from rail_vehicle.virtual_trials import TrialPlan, load_trial, rerun_trial, run_trial, save_trial, trial_history, trial_pdf


def test_trial_recomputes_best_candidate_and_archive(tmp_path):
    case = CoupledCase(duration_s=2)
    plan = TrialPlan((20, 25), (.8, 1), (1,), 5, 20)
    report, refined = run_trial(case, plan)
    assert len(report["candidates"]) == 4
    recommendation = report["recommendation"]
    assert recommendation["solver"]["substeps"] == 10
    proven = [c for c in report["step_checks"] if not c["violations"]]
    assert recommendation["metrics"]["body_y_rms_m_s2"] == min(c["metrics"]["body_y_rms_m_s2"] for c in proven)
    assert refined["evidence_id"] == recommendation["metrics"]["evidence_id"]
    assert len(trial_pdf(report)) > 2000
    by_axis = {row["axis"]: row for row in report["sensitivity"]}
    assert by_axis["speed_m_s"]["matched_pairs"] == 2
    assert by_axis["stiffness_factor"]["matched_pairs"] == 2
    rows = report["candidates"]
    expected = sum((rows[j+1]["metrics"]["body_y_rms_m_s2"]-rows[j]["metrics"]["body_y_rms_m_s2"])/.2
                   for j in (0, 2))/2
    assert by_axis["stiffness_factor"]["mean"] == pytest.approx(expected)

    db = tmp_path / "prior-version.db"
    with get_connection(db) as connection:
        connection.execute("CREATE TABLE previous_notes(note TEXT)")
        connection.execute("INSERT INTO previous_notes VALUES ('retained')")
    init_db(db)
    save_trial(report, db)
    save_trial(report, db)
    assert [row["study_id"] for row in trial_history(db)] == [report["study_id"]]
    saved = load_trial(report["study_id"], db)
    rerun, _ = rerun_trial(saved)
    assert rerun["study_id"] == report["study_id"]
    with pytest.raises(ValueError, match="摘要不一致"):
        save_trial({**report, "source": "measurement"}, db)
    with get_connection(db) as connection:
        assert connection.execute("SELECT note FROM previous_notes").fetchone()[0] == "retained"
        connection.execute("UPDATE coupled_trial_history SET report_json = ?", ('{"source":"tampered"}',))
    with pytest.raises(ValueError, match="归档试验证据摘要不一致"):
        load_trial(report["study_id"], db)

    view = playback(refined, "rear", gain=4)
    index = view["coupled"]["body_peak_sample"]
    assert view["coupled"]["index"] == 1
    assert view["channels"]["rear_wheel_m"][index] == refined["q"][index][9]
    assert view["channels"]["accel_m_s2"][index] == refined["ddq"][index][10]
    assert 0 < view["coupled"]["display_gain"] <= 4


def test_infeasible_checkpoint_and_demo_write_protection(tmp_path, monkeypatch):
    case = CoupledCase(duration_s=2)
    report, response = run_trial(case, TrialPlan((20,), (1,), (1,), .01, .01))
    assert response is None
    assert report["recommendation"] is None
    assert report["candidates"][0]["violations"] == ["roll", "travel"]
    incomplete = TrialPlan((2, 20), (1,), (1,))
    mixed, _ = run_trial(case, incomplete)
    assert mixed["candidates"][0]["violations"] == ["incomplete_excitation"]
    assert mixed["recommendation"]["index"] == 1
    no_input, _ = run_trial(replace(case, track="level"), TrialPlan((20,), (1,), (1,)))
    assert no_input["candidates"][0]["violations"] == ["no_lateral_input"]
    assert no_input["recommendation"] is None
    cancel = Event()

    def checkpoint():
        if cancel.is_set():
            raise TimeoutError
        cancel.set()

    with pytest.raises(TimeoutError):
        run_trial(case, TrialPlan((20,)), checkpoint=checkpoint)
    monkeypatch.setenv("RAIL_PUBLIC_DEMO", "true")
    with pytest.raises(ValueError, match="公开演示"):
        save_trial(report, tmp_path/"not-created.db")
    assert not (tmp_path/"not-created.db").exists()
    for invalid in ((), (float("nan"),), (True,), (20, 20), (1, 3, 4, 5)):
        with pytest.raises(ValueError):
            TrialPlan(invalid)


def test_offline_copilot_runs_scan_report_and_isolates_case():
    case = CoupledCase(duration_s=2, speed_m_s=25)
    prompt = ("比较不同二系刚度和阻尼，刚度倍数 0.8、1，阻尼倍数 1，"
              "侧滚不超过 5 mrad，横向行程不超过 15 mm，并生成试验报告")
    plan = local_plan(prompt)
    assert [p["tool"] for p in plan] == ["optimize_coupled", "coupled_report"]
    assert plan[0]["args"]["max_travel_mm"] == 15
    assert plan[0]["args"]["stiffness"] == [.8, 1]
    assert local_plan("再把行程上限改为 8 mm 复算", plan[:1])[0]["args"]["max_travel_mm"] == 8
    with pytest.raises(ValueError):
        validate_plan([{**plan[0], "args": {**plan[0]["args"], "stiffness": [0]}}])
    chat = Conversation(("unbound",))
    turn = chat.begin(prompt, "offline")
    job = Job()
    job.run(Work(None, coupled_case=case), prompt, [])
    job.drain(turn)
    assert turn.status == "complete", turn.message
    first, second = [entry["result"] for entry in turn.results]
    study = first.provenance["coupled_trial"]
    assert first.scene["transport"]["seconds"] == study["recommendation"]["metrics"]["peak_y_time_s"]
    assert first.scene["simulation"]["parameters"]["evidence_id"] == study["recommendation"]["metrics"]["evidence_id"]
    assert study["request"]["case"]["case"]["speed_m_s"] == 25
    assert second.pdf.startswith(b"%PDF-")
    assert second.provenance["coupled_trial"]["study_id"] == study["study_id"]
    assert read_engineering_context(export_chat(chat, coupled_case=case)) == {"cp_coupled_case": case}
    with pytest.raises(ValueError, match="此会话"):
        list(execute(Work(None, coupled_case=replace(case, speed_m_s=40)), plan[1:]))
