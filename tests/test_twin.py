from dataclasses import replace
import json
from math import isfinite
from threading import Event
from time import monotonic, sleep

import numpy as np
import pytest

from rail_vehicle.coupled_vehicle import simulate, summary
from rail_vehicle.db import get_connection, init_db
from rail_vehicle.twin_data import compare, fuse, parse_observations, quality
from rail_vehicle.twin_identification import coefficients, identify
from rail_vehicle.twin_monitoring import residual_profile, suspension_hypotheses
from rail_vehicle.twin_optimization import OptimizationPlan, optimize, replay_cases
from rail_vehicle.twin_workflow import (DEMO_RECIPE, Request, Research, demo_plan, demonstration, history,
                                        read_research, rerun_request, research_pdf, save_research)


@pytest.fixture(scope="module")
def demo():
    return demonstration()


def test_asynchronous_alignment_preserves_source_lines_and_validates_input(demo):
    case,(train,content,metadata),_,_=demo
    comparison=compare(train,simulate(case))
    series=train.channels["body_y_accel"]
    assert quality(series)["inferred_hz"] == pytest.approx(65)
    row=comparison["channels"]["body_y_accel"]
    assert row["time_s"] == pytest.approx(series.time)
    assert row["csv_lines"] == list(series.lines)
    assert fuse([-1,series.time[10],4],train,["body_y_accel"])["body_y_accel"] == [
        None,{"value":series.values[10],"csv_line":series.lines[10],"observed_time_s":series.time[10]},None]
    with pytest.raises(ValueError,match="激励摘要"):
        compare(train,simulate(replace(case,speed_m_s=22)))
    bad=dict(metadata["channels"]["body_y_accel"],clock_scale=.7)
    with pytest.raises(ValueError,match="时钟"):
        parse_observations(content,{**metadata,"channels":{**metadata["channels"],"body_y_accel":bad}})
    with pytest.raises(ValueError,match="重复"):
        parse_observations(content+content.splitlines()[1]+b"\n",metadata)
    with pytest.raises(ValueError,match="CSV 第"):
        parse_observations(content.replace(b"m/s^2",b"rad",1),metadata)


def test_identification_recovers_known_parameters_under_noise(demo):
    pytest.importorskip("scipy.optimize")
    case,(train,*_),held,(validation,*_)=demo
    plan=demo_plan(case)
    fit=identify(case,train,held,validation,plan.bounds)
    assert fit["flags"] == []
    assert fit["rank"] == 3 and fit["evaluations"] <= fit["budget"]
    assert fit["validation_normalized_rmse"] < .045
    assert fit["validation_normalized_rmse"] < .25*fit["baseline_normalized_rmse"]
    for key,true in {"secondary_y_k_n_m":142000,"secondary_y_c_ns_m":21000,"antiroll_k_nm_rad":250000}.items():
        assert fit["identified"][key] == pytest.approx(true,rel=.04)
        assert fit["conditional_95_interval"][key][0] < true < fit["conditional_95_interval"][key][1]
    nominal=coefficients(case,fit["identified"])
    # Full 19-DOF results agree with the reduced solve at the same refined time grid.
    full=compare(validation,simulate(coefficients(held,fit["identified"]),substeps=10))
    assert full["channels"]["body_y_accel"]["metrics"]["rmse"] == pytest.approx(
        fit["validation_fitted"]["body_y_accel"]["rmse"],rel=2e-8)
    assert nominal.vehicle == case.vehicle


def test_identification_rejects_nonindependence_unexcited_and_missing_directions(demo):
    pytest.importorskip("scipy.optimize")
    case,(train,*_),held,(validation,*_)=demo
    plan=demo_plan(case)
    with pytest.raises(ValueError,match="不同记录"):
        identify(case,train,case,train,plan.bounds)
    with pytest.raises(ValueError,match="同车"):
        identify(case,train,held,replace(validation,vehicle_ref="另一个车辆"),plan.bounds)
    channels={k:v for k,v in validation.channels.items() if k!="body_roll"}
    with pytest.raises(ValueError,match="侧滚角"):
        identify(case,train,held,replace(validation,channels=channels),plan.bounds)
    stagnant=replace(train.channels["body_y_accel"],values=(0.,)*len(train.channels["body_y_accel"].values))
    with pytest.raises(ValueError,match="足够变化"):
        identify(case,replace(train,channels={**train.channels,"body_y_accel":stagnant}),held,validation,plan.bounds)


@pytest.mark.parametrize("strategy",("grid","local","global","hybrid"))
def test_budget_frontier_and_independent_recalculation(demo,strategy):
    case,*_=demo
    plan=replace(demo_plan(case),strategy=strategy,budget=15,tracks=("bump",))
    result=optimize(case,plan)
    assert 3 <= result["evaluations"] <= 15
    assert result["pareto"]
    for i,row in enumerate(result["pareto"]):
        assert row["substeps"]==10 and not row["violations"]
        assert row["worst"]["roll_peak_mrad"] <= plan.max_roll_mrad
        assert row["worst"]["secondary_y_peak_mm"] <= plan.max_travel_mm
        before,after=replay_cases(case,result,candidate=i)
        check=simulate(after,substeps=10)
        assert summary(check)==row["conditions"][0]["metrics"]
        assert simulate(before,substeps=10)["configuration_sha256"]==result["baseline"]["conditions"][0]["sha256"]
    assert all(isfinite(v) for v in result["first_choice"]["worst"].values())


def test_optimizer_rejects_incomplete_axle_run_and_returns_infeasible(demo):
    case,*_=demo
    plan=demo_plan(case)
    with pytest.raises(ValueError,match="尾轴"):
        optimize(case,replace(plan,speeds=(2.,)))
    report=optimize(case,replace(plan,tracks=("bump",),budget=12,max_roll_mrad=.01,max_travel_mm=.01))
    assert not report["pareto"] and report["first_choice"] is None
    assert all(row["violations"] for row in report["coarse"])


def _finish(task):
    deadline=monotonic()+40
    while task.state()[0] in ("running","paused","cancelling") and monotonic()<deadline:
        sleep(.02)
    return task.state()


def test_approved_workflow_links_reports_and_replays_archive(tmp_path,demo):
    pytest.importorskip("scipy.optimize")
    case,(train,*_),held,(validation,*_)=demo
    plan=replace(demo_plan(case),tracks=("bump",),budget=13)
    task=Research(Request(case,train,held,validation,plan,recipe=DEMO_RECIPE))
    assert task.state()==("draft",0,None,None)
    task.start()
    status,stage,error,report=_finish(task)
    assert status=="complete",error
    assert report["identification"]["identified"]
    assert report["optimization"]["first_choice"]
    assert report["optimization"]["case"]["case"]["transverse"]["secondary_y_k_n_m"]==report["identification"]["identified"]["secondary_y_k_n_m"]
    assert research_pdf(report).startswith(b"%PDF-")
    db=tmp_path/"test.db"
    init_db(db)
    save_research(report,db)
    save_research(report,db)
    evidence=history(db)[0][0]
    assert len(history(db))==1
    loaded=read_research(evidence,db)
    rerun=Research(rerun_request(loaded))
    rerun.start()
    assert _finish(rerun)[3]["evidence_id"]==evidence
    with get_connection(db) as connection:
        connection.execute("UPDATE twin_research_history SET report_json=?",(json.dumps({**report,"source":"changed"}),))
    with pytest.raises(ValueError,match="摘要"):
        read_research(evidence,db)


def test_pause_resume_and_cancel_are_enforced(demo,monkeypatch):
    case,(train,*_),held,(validation,*_)=demo
    entered,go=Event(),Event()
    def stall(case,data):
        entered.set()
        assert go.wait(4)
    monkeypatch.setattr("rail_vehicle.twin_workflow.check_case",stall)
    task=Research(Request(case,train,held,validation,demo_plan(case)))
    task.start()
    try:
        assert entered.wait(3)
        task.pause()
        go.set()
        assert task.state()[:2]==("paused",0)
        task.start()
        assert task.state()[0]=="running"
        task.cancel()
        assert _finish(task)[0]=="cancelled"
    finally:
        go.set()


def test_residual_screening_retains_peak_line_and_refuses_irregular_fft(demo):
    case,(train,*_),*_=demo
    pair=compare(train,simulate(case))["channels"]["body_y_accel"]
    review=residual_profile(pair,.01)
    assert len(review["windows"])==4
    assert sum(w["flagged"] for w in review["windows"])==len(review["flagged_csv_lines"])
    assert review["peak"]["csv_line"] in pair["csv_lines"]
    assert review["spectrum"] is None and review["spectrum_exclusion"]
    candidates=suspension_hypotheses(case,train,"body_y_accel")
    assert candidates["claim_scope"]=="teaching_hypotheses_only"
    assert len({k["evidence_id"] for k in candidates["scenarios"]})==3
