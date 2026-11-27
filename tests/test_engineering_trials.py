from dataclasses import replace
import json

import numpy as np
import pytest

from rail_vehicle.copilot.context import Dataset
from rail_vehicle.copilot.tools import Work
from rail_vehicle.db import get_connection, init_db
from rail_vehicle.demo_data import analysis_demo_data
from rail_vehicle.engineering_trials import (HYPOTHESES, Trial, check_binding, history, lateral_evidence,
                                             lateral_intent, load_trial, read_request, save_request, solve_trial,
                                             trial_report_pdf, vary)
from rail_vehicle.manchester_contact import compare_loaded_compliance
from rail_vehicle.nonlinear_vehicle import VehicleSystem, WheelRailCase, case_config, read_case
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_track import template
from rail_vehicle.wheelrail_playback import playback


def demo_work():
    vehicle,batches,rows=analysis_demo_data()
    return Work(Dataset("demo",vehicle,batches[0],rows[batches[0]["id"]]))


def test_numerical_hypotheses_change_only_the_expected_lateral_coefficients():
    case=WheelRailCase()
    nominal=VehicleSystem(case)
    assert not any(name.endswith("_lateral_k_scale") for name in case_config(case)["case"])
    for param,value in HYPOTHESES:
        changed=vary(case,param,value)
        new=VehicleSystem(changed)
        np.testing.assert_array_equal(new.vertical,nominal.vertical)
        np.testing.assert_array_equal(new.k_yaw,nominal.k_yaw)
        if param=="secondary_c":
            np.testing.assert_allclose(new.c_y,nominal.c_y*np.array([value,value,1,1,1,1]))
            np.testing.assert_array_equal(new.k_y,nominal.k_y)
        else:
            mask=(np.arange(6)<2) if param=="secondary_k" else (np.arange(6)>=2)
            np.testing.assert_allclose(new.k_y,nominal.k_y*np.where(mask,value,1))
            np.testing.assert_array_equal(new.c_y,nominal.c_y)
        assert read_case(json_text(case_config(changed)))==changed
    assert vary(case,"radius",650.).route.sections[-1].end_curvature_m_inv==pytest.approx(1/650)
    assert vary(case,"load",1.1).vehicle.body_mass_kg==pytest.approx(1.1*case.vehicle.body_mass_kg)
    assert vary(case,"cant",75.).route.sections[-1].end_cant_m==.075
    assert vary(case,"irregularity",.5).route.left_m==case.route.left_m*.5


def test_observed_event_is_an_immutable_reference_for_the_requested_trial():
    assert lateral_intent("分析当前车辆横向振动异常，设计三组对照仿真")
    assert not lateral_intent("找出所有横向加速度超阈值区段")
    work=demo_work()
    evidence=lateral_evidence(work)
    assert evidence["event_id"]=="event.lateral.001" and evidence["index_1based"]==[31,33]
    assert evidence["peak_m_s2"]==1.8 and evidence["source"]=="demo"
    request=Trial(WheelRailCase(),HYPOTHESES,observation=evidence)
    check_binding(request,work.data)
    other=replace(work.data,threshold=.8)
    with pytest.raises(ValueError,match="快照不同"):
        check_binding(request,other)
    with pytest.raises(ValueError,match="没有横向"):
        lateral_evidence(Work(replace(work.data,threshold=2.5)))
    assert read_request(json.loads(json_text(request.payload())))==request
    changed=json.loads(json_text(request.payload()))
    changed["observation"]["peak_m_s2"]=200
    with pytest.raises(ValueError,match="摘要"):
        read_request(changed)


def test_approved_request_is_durable_and_tampered_sqlite_row_fails(tmp_path):
    path=tmp_path/"trial.db"
    init_db(path)
    request=Trial(WheelRailCase(),HYPOTHESES)
    save_request(request,path)
    save_request(request,path)
    records=history(path)
    assert len(records)==1 and not records[0]["finished"]
    trial_id=records[0]["trial_id"]
    assert load_trial(trial_id,path)==(request,None)
    with get_connection(path) as conn:
        conn.execute("UPDATE engineering_trial_records SET request_json='{}' WHERE trial_id=?",(trial_id,))
    with pytest.raises(ValueError,match="摘要"):
        load_trial(trial_id,path)


def test_scan_is_recomputed_and_the_peak_replay_uses_same_trajectory():
    pytest.importorskip("scipy")
    base=WheelRailCase(route=template("straight",left=.0002,right=.0001,align=.00015),duration_s=1.)
    request=Trial(base,(("secondary_c",.75),("secondary_c",1.25)))
    results={}
    report=solve_trial(request,capture=lambda trace:results.update({trace["evidence_id"]:trace}))
    assert report["solves"]==5 and report["sensitivity"] is not None
    compare=report["comparison"]
    assert compare["eligible"] and compare["candidate"]["solver"]["method"]=="RK45"
    row=report["cases"][0]
    curve=results[row["response_id"]]
    assert curve["time_s"][np.argmax(np.abs(np.asarray(curve["ddq"])[:,0]))]==row["peak_time_s"]
    rendering=playback(curve,initial_focus="front_secondary")
    assert rendering["advanced"]["initial_focus"]=="front_secondary"
    np.testing.assert_allclose(rendering["channels"]["accel_m_s2"],np.asarray(curve["ddq"])[:,0])
    assert trial_report_pdf(report).startswith(b"%PDF-")


def test_rejects_duplicate_plans_out_of_range_variation_and_unknown_loaded_reference():
    with pytest.raises(ValueError,match="重复"):
        Trial(WheelRailCase(),(("load",1.),))
    with pytest.raises(ValueError,match="实验区间"):
        Trial(WheelRailCase(),(("speed",100.),))
    with pytest.raises(ValueError,match="需要曲线"):
        Trial(WheelRailCase(route=template("straight")),(("cant",90.),))
    with pytest.raises(ValueError,match="摘要"):
        compare_loaded_compliance(b"10 kN",80e9)
