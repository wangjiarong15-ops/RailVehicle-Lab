from dataclasses import replace

import numpy as np
import pytest

from rail_vehicle.coupled_vehicle import MODEL as LINEAR
from rail_vehicle.dynamics_registry import compare_common_vertical
from rail_vehicle.nonlinear_vehicle import MODEL, WheelRailCase
from rail_vehicle.pages.wheelrail_lab import prompt_plan, wheelrail_intent
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheelrail_study import StudyPlan, restore_study, solve_study, study_pdf


def test_copilot_read_only_prompt_and_budget():
    prompt="建立曲线通过试验，速度 20、22 m/s，半径 450、650 m，二系垂向行程不超过 35 mm，预算 24 次"
    assert wheelrail_intent(prompt)
    plan=prompt_plan(prompt)
    assert (plan.speeds,plan.radii,plan.max_travel_mm)==((20,22),(450,650),35)
    assert not wheelrail_intent("轮轨是什么？")
    with pytest.raises(ValueError,match="至少需 24"):
        prompt_plan(prompt.replace("24 次","16 次"))
    with pytest.raises(ValueError,match="各扫描轴"):
        prompt_plan(prompt.replace("速度 20","速度 -20"))
    with pytest.raises(ValueError):
        replace(plan,speeds=(22.,22.))
    with pytest.raises(ValueError,match="时窗不足"):
        solve_study(WheelRailCase(duration_s=1.),plan)


def test_finite_budget_independent_recalculation_and_archive_checks():
    pytest.importorskip("scipy")
    case=WheelRailCase()
    plan=StudyPlan(speeds=(20.,),radii=(450.,),repairs=(.75,),right_factor=.9,max_travel_mm=35,maximum_solves=5)
    phases=[]
    traces=[]
    result=solve_study(case,plan,stage=phases.append,capture=traces.append)
    assert phases==list(range(4))
    assert result["solves"]==5 and result["comparison"]["qualified"]
    old,new=(result["comparison"][key] for key in ("reference","candidate"))
    assert old["rows"][0]["solver"]["method"]==new["rows"][0]["solver"]["method"]=="RK45"
    assert [item["evidence_id"] for item in traces]==[whole["rows"][0]["evidence_id"] for whole in (old,new)]
    assert all(len(item["time_s"])==301 for item in traces)
    assert new["worst_body_y_rms_m_s2"]<old["worst_body_y_rms_m_s2"]
    assert new["max_travel_mm"]<old["max_travel_mm"]<35
    assert restore_study(json_text(result))==(case,plan,result["evidence_id"])
    result["plan"]["max_travel_mm"]=100
    with pytest.raises(ValueError,match="SHA-256"):
        restore_study(json_text(result))
    assert study_pdf(result).startswith(b"%PDF-")


def test_registered_models_solve_shared_input_on_independent_native_grids():
    pytest.importorskip("scipy")
    linear,nonlinear=compare_common_vertical()
    assert (linear["model"],nonlinear["model"])==(LINEAR,MODEL)
    for result in (linear,nonlinear):
        t,y=(np.asarray(result[key]) for key in ("time_s","body_z_accel"))
        assert t[0]==0 and t[-1]==3 and len(t)==len(y)
        assert .035<np.max(y)<.05
    mapped=np.interp(nonlinear["time_s"],linear["time_s"],linear["body_z_accel"])
    assert 1e-6<np.max(abs(mapped-nonlinear["body_z_accel"]))<.015
