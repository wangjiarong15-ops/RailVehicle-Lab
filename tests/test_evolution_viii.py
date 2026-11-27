from dataclasses import replace
from math import pi

import numpy as np
import pytest

from rail_vehicle.contact_calibration import circular_hertz, compare_fixed_grid, fit_compliance, load_reference, manchester_points
from rail_vehicle.db import get_connection, init_db
from rail_vehicle.nonlinear_vehicle import VehicleSystem, WheelRailCase
from rail_vehicle.research_agent import Plan, approved, candidate, design_points, execute, graph, read_plan, restore
from rail_vehicle.twin_identification import identify
from rail_vehicle.twin_workflow import demo_plan, demonstration
from rail_vehicle.wheel_rail import normal_energy, normal_force
from rail_vehicle.wheelrail_playback import playback


def test_geometry_uses_matching_physical_quantities_and_disjoint_groups():
    pytest.importorskip("scipy")
    ref=load_reference()
    rows=ref["manchester"]
    assert len(rows)==42 and len({r["shift_mm"] for r in rows})==21
    left=rows[0]
    assert left["vertical_force_n"]==10000 and left["patch_normal_n"]==9997
    assert left["track_y_mm"]==-751.8726 and left["peak_pressure_mpa"]==338.3
    assert left["grid_area_mm2"]==pytest.approx(1291*.2**2)
    report=fit_compliance(rows)
    assert report["effective_n_m32"]/1e9==pytest.approx(97.327,rel=1e-4)
    assert not set(report["errors"]["fit"]["cases"]) & set(report["errors"]["held_out"]["cases"])
    held=report["errors"]["held_out"]
    assert all(new<before for new,before in zip(held["fitted_rmse_relative"],held["initial_rmse_relative"]))
    assert abs(report["centred_left"]["fitted_relative_error"][1])>.3
    comparison=compare_fixed_grid(ref["module3"])
    assert comparison["fit"]["normal_n"]==42083
    assert comparison["held_out"]["normal_n"]==19563
    assert comparison["held_out_fitted_mm"]==pytest.approx(.03,abs=.00015)
    assert not comparison["reference_tangent_available"]
    with pytest.raises(ValueError,match="摘要"):
        manchester_points(b"invalid benchmark")
    with pytest.raises(ValueError,match="留出"):
        fit_compliance(rows[:4])


def test_circular_contact_force_energy_tangent_and_pressure_integration():
    radius=.46
    loads=[1000.,10000.,40000.]
    e=210e9/(2*(1-.28**2))
    coefficient=4*e*radius**.5/3
    for load in loads:
        result=circular_hertz(load,radius,210e9,.28,210e9,.28)
        delta=result["approach_m"]
        assert normal_force(coefficient,delta)==pytest.approx(load)
        increment=delta/1e4
        force=(normal_energy(coefficient,delta+increment)-normal_energy(coefficient,delta-increment))/(2*increment)
        tangent=(normal_force(coefficient,delta+increment)-normal_force(coefficient,delta-increment))/(2*increment)
        assert force==pytest.approx(load,rel=1e-6)
        assert tangent==pytest.approx(result["tangent_n_m"],rel=1e-6)
        x=np.linspace(0,1,4097)
        integrate=getattr(np,"trapezoid",None) or np.trapz
        assert 2*pi*result["radius_m"]**2*result["peak_pressure_pa"]*integrate(x*np.sqrt(1-x*x),x)==pytest.approx(load,rel=5e-6)
    assert not normal_force(coefficient,-.001)
    assert circular_hertz(0.,radius,210e9,.28,210e9,.28)["peak_pressure_pa"]==0.


def test_factorial_and_lhs_are_replayable_and_budgeted():
    base=WheelRailCase(duration_s=1.)
    plan=Plan(base)
    assert len(design_points(plan))==4 and len(graph(plan))==13
    assert read_plan(plan.payload())==plan
    lhs=replace(plan,design="lhs",draws=5,budget=10)
    coordinates=design_points(lhs)
    for axis,lo,hi in lhs.axes:
        assert sorted(int((point[axis]-lo)*5/(hi-lo)) for point in coordinates)==list(range(5))
    with pytest.raises(ValueError,match="最多需"):
        replace(plan,budget=6)
    with pytest.raises(ValueError,match="摘要"):
        read_plan({**plan.payload(),"budget":12})
    new=VehicleSystem(candidate(base,{"primary_c":.8}))
    np.testing.assert_allclose(new.c_y,np.asarray([16000.,16000.,25600.,25600.,25600.,25600.]))


def test_real_research_recovers_saved_steps_and_builds_synchronized_comparison(tmp_path):
    pytest.importorskip("scipy")
    db=tmp_path/"isolated.db"
    init_db(db)
    plan=Plan(WheelRailCase(duration_s=1.25),axes=(("secondary_k",.75,1.10),),robustness=False,budget=5)
    task=approved(plan,db)

    def fail_here(step):
        if step=="baseline":
            raise InterruptedError

    with pytest.raises(InterruptedError):
        execute(task,db,progress=fail_here)
    _, checkpoint=restore(task,db)
    assert list(checkpoint)==["observe","knowledge","baseline"]
    done=execute(task,db)
    assert done["baseline"]==checkpoint["baseline"]
    report=done["report"]
    assert report["ranking"]["choice"] is not None
    assert report["difference"]["supported"]
    assert [step["response_id"] for step in report["tools"] if step["tool"]=="independent_response"]
    payload=playback(done["verify_base"]["response"],done["verify_choice"]["response"])
    assert payload["parameters"]["alternative"]==done["verify_choice"]["response"]["evidence_id"]
    assert execute(task,db)["report"]==restore(task,db)[1]["report"]
    with get_connection(db) as connection:
        connection.execute("UPDATE engineering_agent_steps SET sha256='tampered' WHERE step_id='observe'")
    with pytest.raises(ValueError,match="摘要"):
        restore(task,db)


def test_joint_spectral_fit_on_distinct_noisy_observation():
    pytest.importorskip("scipy")
    case,(train,*_),held,(validation,*_)=demonstration()
    report=identify(case,train,held,validation,demo_plan(case).bounds,spectral_weight=.5)
    assert report["identified"]["secondary_y_k_n_m"]==pytest.approx(142000,rel=.02)
    assert report["identified"]["secondary_y_c_ns_m"]==pytest.approx(21000,rel=.02)
    assert report["validation_normalized_rmse"]<report["baseline_normalized_rmse"]/2
    assert report["spectral"]["fitted_rmse_si"]["body_y_accel"] < report["spectral"]["baseline_rmse_si"]["body_y_accel"]/2
    assert report["conditional_95_interval"] is None
