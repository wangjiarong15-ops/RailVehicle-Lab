from dataclasses import replace
import json

import numpy as np
import pytest

from rail_vehicle.nonlinear_suspension import Mount, dissipative_force, elastic_force, mount_bank, potential
from rail_vehicle.nonlinear_vehicle import (DOFS, VehicleSystem, WheelRailCase, case_config, frequency_response,
                                             performance, read_case, run_vehicle, tangent_stability)
from rail_vehicle.wheel_rail import (Contact, G, isolated_wheelset, klingel_wavelength, normal_energy, normal_force,
                                     wheel_forces, wheel_generalized, wheelset_linear_matrix)
from rail_vehicle.wheel_track import Section, SpatialTrack, Track, body_acceleration, template
from rail_vehicle.wheelrail_playback import playback


def test_unilateral_hertz_energy_and_friction_circle():
    p=Contact()
    delta=np.array([-.0001,0,.0003,.0012])
    q=normal_force(p.normal_n_m32,delta)
    np.testing.assert_array_equal(q[:2],[0,0])
    slope=(normal_energy(p.normal_n_m32,delta+1e-9)-normal_energy(p.normal_n_m32,delta-1e-9))/2e-9
    np.testing.assert_allclose(slope[2:],q[2:],rtol=5e-8)
    spec=wheel_forces(p,[.009,0,.002,.012],[.12,0,0,.09],20.,0.,0.,np.zeros(2),.00075)
    assert spec["normal"][0]==0
    assert spec["fx"][0]==spec["fy"][0]==0
    assert spec["flange"]<0
    assert spec["rolling_radius"][0]>spec["rolling_radius"][1]
    assert np.all(np.hypot(spec["fx"],spec["fy"]) <= p.friction*spec["normal"]*(1+1e-12))
    assert np.sum(spec["fx"]*spec["xi_x"]+spec["fy"]*spec["xi_y"])<0


def test_contact_switch_matches_free_fall_and_an_implicit_solver():
    integrate=pytest.importorskip("scipy.integrate")
    p=Contact()
    mass,height=1550.,.003
    indent=(mass*G/(2*p.normal_n_m32))**(2/3)

    def rhs(t,state):
        z,speed=state
        wheels=wheel_forces(p,[0,z,0,0],[0,speed,0,0],20.,0.,0.,np.zeros(2),indent)
        return [speed,wheels["normal"].sum()/mass-G]

    def boundary(t,state):
        return state[0]-indent

    settings=dict(t_span=(0,.055),y0=(indent+height,0.),t_eval=np.linspace(0,.055,551),
                  rtol=1e-10,atol=1e-12,max_step=.00012,events=boundary)
    explicit=integrate.solve_ivp(rhs,method="DOP853",**settings)
    implicit=integrate.solve_ivp(rhs,method="Radau",**settings)
    assert explicit.success and implicit.success
    assert len(explicit.t_events[0])==len(implicit.t_events[0])==2
    assert explicit.t_events[0][0]==pytest.approx(np.sqrt(2*height/G),abs=1e-9)
    np.testing.assert_allclose(explicit.t_events[0],implicit.t_events[0],atol=1e-8,rtol=0)
    np.testing.assert_allclose(explicit.y[0],implicit.y[0],atol=1e-8,rtol=0)
    z,speed=explicit.y
    total=.5*mass*speed**2+mass*G*(z-indent)+2*normal_energy(p.normal_n_m32,indent-z)
    np.testing.assert_allclose(total/total[0],1.,atol=1e-6,rtol=0)


def test_wheelset_analytic_tangent_and_kinematic_wavelength():
    p=replace(Contact(),law="linear")
    speed=20.
    matrix=wheelset_linear_matrix(p,speed)
    def response(x):
        forces=wheel_forces(p,[x[0],0,0,x[1]],[x[2],0,0,x[3]],speed,0.,0.,np.zeros(2),.0008)
        a=wheel_generalized(p,forces)
        return np.array([x[2],x[3],a[0]/1550.,a[3]/760.])
    numerical=np.column_stack([response(x*1e-6)/1e-6 for x in np.eye(4)])
    np.testing.assert_allclose(matrix,numerical,rtol=1e-8,atol=1e-10)
    assert klingel_wavelength(p) == pytest.approx(2*np.pi*np.sqrt(.75*.42/.1))
    assert klingel_wavelength(replace(p,conicity=0)) is None
    pytest.importorskip("scipy.integrate")
    trace=isolated_wheelset(p,speed,.4,.001)
    assert len(trace["state"])==81 and trace["state"][-1][0] != trace["state"][0][0]


def test_nonlinear_mount_energy_force_and_asymmetric_stops():
    bank=mount_bank([Mount(600_000,12_000),Mount(420_000,9000,stop_m=.07)])
    d=np.array([.10,-.09])
    offset=np.array([-.055,-.055])
    force=elastic_force(bank,d,offset)
    central=(potential(bank,d+1e-7,offset)-potential(bank,d-1e-7,offset))/2e-7
    np.testing.assert_allclose(force,central,rtol=1e-8)
    applied=dissipative_force(bank,np.array([.3,-.2]))
    assert applied @ np.array([.3,-.2])>0
    assert np.any(abs(force)>abs(bank[0]*(d+offset)))


def test_route_transition_heading_cant_balance_and_scene_rails():
    route=Track((Section(10.,0.,0.),Section(20.,1/500,.1),Section(100.,1/500,.1)),
                left_m=0,right_m=0,align_m=0)
    profile=SpatialTrack(route)
    np.testing.assert_allclose(profile.bearing([0,10,20,30,50]),[0,0,.005,.02,.06],atol=1e-12)
    k,_,bank,_,_=profile.map(np.array([50.]),.75)
    balance=np.sqrt(-G*np.tan(bank[0])/k[0])
    assert body_acceleration(balance,k,bank)[0] == pytest.approx(0.,abs=1e-12)
    scene=profile.scene(np.arange(-4,56,.1),.75)
    rails=np.array(scene["rails"])
    np.testing.assert_allclose(np.linalg.norm(rails[:,0]-rails[:,1],axis=1),1.5,atol=1e-12)
    assert rails[-1,1,2]>rails[-1,0,2]
    assert scene["center_xy"][-1][1]>0


def test_full_static_equilibrium_resolves_asymmetric_preload():
    pytest.importorskip("scipy.optimize")
    symmetric=WheelRailCase()
    system=VehicleSystem(symmetric)
    q,res,loads=system.equilibrium()
    assert np.max(abs(q))<1e-9 and res<1e-9
    expected=G*(symmetric.vehicle.body_mass_kg+2*symmetric.vehicle.bogie_mass_kg+4*symmetric.vehicle.wheelset_mass_kg)
    assert loads.sum()==pytest.approx(expected,rel=1e-9)
    asym=replace(symmetric,secondary_right=replace(symmetric.secondary_right,stiffness_n_m=260_000))
    non=VehicleSystem(asym)
    q,res,loads=non.equilibrium()
    assert res<1e-8 and abs(q[2])>.001 and loads.sum()==pytest.approx(expected,rel=1e-8)
    # A centred, laterally unloaded vehicle can lean while the total rail reactions stay balanced.
    np.testing.assert_allclose(loads[:,0],loads[:,1],atol=1e-5)
    _,evidence=non.evaluate(-2.,q,np.zeros(len(DOFS)),metrics=True)
    assert abs(evidence["mount_z_m"][0]-evidence["mount_z_m"][1])>.02
    assert read_case(json.dumps(case_config(asym)))==asym


def test_transient_agrees_with_independent_fixed_step_rk():
    pytest.importorskip("scipy.integrate")
    case=WheelRailCase(route=template("straight",left=.0002,right=.0001,align=.0002),duration_s=1.)
    standard=run_vehicle(case)
    refined=run_vehicle(case,accuracy="refined")
    system=VehicleSystem(case)
    q,_,_=system.equilibrium()
    q[15]+=case.disturbance_m
    reference=np.r_[q,np.zeros(len(DOFS))]
    def f(t,z):
        return np.r_[z[31:],system.evaluate(t,z[:31],z[31:])]
    step=.0002
    for k in range(5000):
        t=k*step
        a=f(t,reference)
        b=f(t+step/2,reference+a*step/2)
        c=f(t+step/2,reference+b*step/2)
        d=f(t+step,reference+c*step)
        reference+=step/6*(a+2*b+2*c+d)
    np.testing.assert_allclose(refined["q"][-1],reference[:31],atol=1e-7)
    assert np.max(np.abs(np.asarray(standard["q"])-np.asarray(refined["q"])))<2e-5
    assert performance(refined)["separated_samples"]==0
    assert performance(refined)["tread_y_over_q"] <= case.contact.friction+1e-10


def test_curve_has_track_delays_and_linearization_changes_with_speed():
    pytest.importorskip("scipy.integrate")
    case=WheelRailCase()
    result=run_vehicle(case)
    position=np.asarray(result["station_m"])
    np.testing.assert_allclose(position[:,3]-position[:,4],2.6)
    np.testing.assert_allclose(position[:,3]-position[:,5],17.)
    assert performance(result)["uncompensated_body_peak_m_s2"]>.1
    analysis=tangent_stability(case,[10.,20.,30.,40.])
    assert analysis["rows"][0]["growth_per_s"]<0
    assert analysis["rows"][-1]["growth_per_s"]>0
    assert analysis["onset_brackets_m_s"]
    onset=analysis["onset_estimates"][0]
    assert 24<onset["speed_m_s"]<25
    low,high=onset["bracket_m_s"]
    assert 0<high-low<=.02
    assert all(mode["natural_hz"]>=mode["damped_hz"] for mode in analysis["rows"][0]["modes"])
    frf=frequency_response(case,[.05,1.,10.])
    assert .9<frf[0]["vertical_gain"]<1.1
    scene=playback(result)["advanced"]
    np.testing.assert_allclose(scene["station"],position)
    np.testing.assert_allclose(scene["tread_normal_n"],result["contact"]["normal"])
    frame=225
    assert scene["pose"][frame][0][5]>0 and scene["contact"][frame][0][1][2]>scene["contact"][frame][0][0][2]
