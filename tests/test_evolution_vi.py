from dataclasses import asdict, replace
from hashlib import sha256
from threading import Thread
import json

import numpy as np
import pytest

from rail_vehicle.flexible_track import TrackCase, linear_matrices, response
from rail_vehicle.manchester_contact import run_external_case
from rail_vehicle.measurement_stream import Acquisition, Device, Packet, Replay, read_packet_csv, read_recording
from rail_vehicle.profile_geometry import Pair, read_profile, sample_profiles
from rail_vehicle.state_monitoring import ObserverCase, diagnosis, estimate, matrices, simulated_stream
from rail_vehicle.stream_loopback import LoopbackCapture
from rail_vehicle.validation_center import Provenance, compare_reference, numerical_reference, read_reference, validation_pdf
from rail_vehicle.validation_uncertainty import study


def test_profile_exact_parabolic_tangent_and_mirror():
    pytest.importorskip("scipy")
    pair=Pair(*(read_profile(item) for item in sample_profiles()))
    shifted=pair.contact(.003)
    assert shifted["rail_y_m"]==pytest.approx(-.016,abs=2e-5)
    assert shifted["angle_deg"]==pytest.approx(np.degrees(np.arctan(.1)),abs=1e-8)
    assert shifted["status"]=="tread"
    left,centre,right=pair.scan([-.002,0.,.002])
    assert left["delta_radius_m"]==pytest.approx(-right["delta_radius_m"])
    assert centre["secant_conicity"] is None
    assert pair.local_conicity()["conicity"]==pytest.approx(.10,abs=1e-8)
    worn=Pair(*(read_profile(item) for item in sample_profiles(worn=True)))
    assert worn.local_conicity()["conicity"]==pytest.approx(.075,abs=1e-8)
    assert pair.contact(.025)["status"] in ("edge","flange")


@pytest.mark.parametrize("raw",[
    b"y,z\n0,0\n1,0\n2,0\n3,0",
    b"y_mm,z_mm\n0,0\n0,1\n1,3\n2,4",
    b"y_mm,z_mm\n0,0\n1,1\n90,2\n91,3",
    b"y_mm,z_mm\n0,0\n1,1\n2,NaN\n3,3",
    b"y_mm,z_mm\n0,0\n1,1\n2,30\n3,31",
])
def test_profile_refuses_bad_units_jumps_and_clocks(raw):
    with pytest.raises(ValueError):
        read_profile(raw)
    with pytest.raises(ValueError,match="摘要"):
        run_external_case(raw,raw)


def test_flexible_track_matches_matrix_exponential_in_small_response():
    linear=pytest.importorskip("scipy.linalg")
    case=TrackCase(contact="linear",defect_depth_m=0.,support_factor=1.)
    m,c,k=linear_matrices(case)
    np.testing.assert_allclose(k,k.T)
    assert np.min(np.linalg.eigvalsh(k))>0
    mat=np.block([[np.zeros((2,2)),np.eye(2)],[-np.linalg.solve(m,k),-np.linalg.solve(m,c)]])
    start=np.array([.000025,0.,0.,0.])
    result=response(case,initial=start)
    expected=np.array([linear.expm(t*mat)@start for t in result["time_s"]])
    np.testing.assert_allclose(result["wheel_z_m"],expected[:,0],atol=2e-10,rtol=0)
    np.testing.assert_allclose(result["rail_z_m"],expected[:,1],atol=2e-10,rtol=0)
    at_rest=response(replace(case,contact="hertz"))
    np.testing.assert_allclose(at_rest["normal_n"],case.static_normal_n,atol=1e-8)


def test_reference_alignment_and_independently_recomputed_load():
    pytest.importorskip("scipy")
    case=TrackCase()
    provenance,blob=numerical_reference(case,"normal_n")
    report=compare_reference(case,"normal_n","N",provenance,blob,1.)
    assert report["rmse"]<.01 and report["within_declared_gate"]
    assert report["source"]["level"]=="numerical"
    assert report["reference_sha256"]==sha256(blob).hexdigest()
    assert validation_pdf(report).startswith(b"%PDF")
    with pytest.raises(ValueError,match="SI"):
        compare_reference(case,"normal_n","kN",provenance,blob,1.)
    with pytest.raises(ValueError,match="递增"):
        read_reference(b"time_s,value\n"+b"0.10,2\n"*12)
    with pytest.raises(ValueError,match="HTTPS"):
        Provenance("external","External trial","test","unknown","test","file:///no")


def test_reconnection_recording_units_and_chronology():
    pytest.importorskip("scipy")
    target,packets,_=simulated_stream()
    replay=Replay(target,packets)
    replay.connected=True
    assert replay.poll(20)==20
    replay.connected=False
    assert replay.poll(100)==20
    replay.connected=True
    assert replay.poll(1000)==320
    assert not replay.connected and target.counters["missing_sequence"]==7
    assert len(target.samples)==320 and target.counters["repeated"]==0
    reload=read_recording(target.recording())
    assert reload.samples==target.samples and reload.evidence_id==target.evidence_id
    assert reload.counters==target.counters
    device=Device("imu","acceleration","g",-1,2,.05,1200,"reviewed","common_trigger")
    check=read_packet_csv(b"device,seq,timestamp_us,value\nimu,0,1200,0.15\n",(device,),0,"declared_test")
    assert check.samples[0]["acceleration"]==pytest.approx(-1.96133)
    check.push(Packet("imu",2,20120,.15))
    assert check.counters["missing_sequence"]==1 and check.counters["unsynchronized"]==0
    check.push(Packet("imu",3,45000,.15))
    assert check.counters["unsynchronized"]==1


def test_observer_unobservable_and_converges_on_independent_synthetic_truth():
    pytest.importorskip("scipy")
    nominal,payload,truth=simulated_stream("nominal")
    for _,packet in payload:
        nominal.push(packet)
    rows=estimate(nominal)
    residuals=[row["estimate_m"]-truth[i] for i,row in enumerate(rows) if row["estimate_m"] is not None and i>20]
    assert np.sqrt(np.mean(np.asarray(residuals)**2))<.0015
    _,_,q,_=matrices(ObserverCase())
    assert np.min(np.linalg.eigvalsh(q))>0
    input_only=Acquisition((nominal.devices["drive"],),nominal.origin,"simulated")
    for _,packet in payload:
        if packet.device=="drive":
            input_only.push(packet)
    assert all(row["estimate_m"] is None for row in estimate(input_only))
    assert diagnosis(input_only)["status"]=="insufficient"


def test_monitoring_uses_holdout_and_quarantines_bad_time():
    pytest.importorskip("scipy")
    target,payload,_=simulated_stream()
    for _,packet in payload:
        target.push(packet)
    report=diagnosis(target)
    assert report["status"]=="candidate" and report["attempts"][0]["hypothesis"]=="等效阻尼降低 38%"
    assert report["attempts"][0]["rmse_m_s2"]<.10
    assert report["window_start_s"]==3.2
    assert report["recording"]==target.evidence_id and report["frequency_hz"]>0
    assert {"加速度零偏","激励比例偏差"}.issubset({item["hypothesis"] for item in report["attempts"]})
    target.push(Packet("imu-z",320,target.origin+320*20_000+8000,0.))
    assert diagnosis(target)["status"]=="insufficient"


def test_uncertainty_reports_paired_draws_and_an_independent_solver():
    pytest.importorskip("scipy")
    results=study(TrackCase(),draws=6,seed=37)
    lo,centre,hi=results["empirical_reduction_n"]
    assert lo<=centre<=hi and len(results["rows"])==6
    assert results["independent"]["solver"]=="Radau"
    assert results["independent"]["difference_n"]<.01
    assert 0<=results["candidate_failure_rate"]<=1


def test_local_websocket_resumes_in_order_without_duplicating_packets():
    websocket=pytest.importorskip("websockets.sync.server")
    source=Acquisition((Device("drive","force","N",1,1.,0.,0,"declared","common_trigger"),),0,"simulated")
    packets=[Packet("drive",i,i*20_000,float(i)) for i in range(4)]
    offsets=[]

    def relay(socket):
        start=json.loads(socket.recv(timeout=3))["resume"]
        offsets.append(start)
        socket.send(json.dumps({"resume":start,"declaration":source.declaration()}))
        end=2 if not start else len(packets)
        for i in range(start,end):
            socket.send(json.dumps({"index":i,"packet":asdict(packets[i])}))
        if start:
            socket.send(json.dumps({"end":end}))

    with websocket.serve(relay,"127.0.0.1",0) as server:
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            url=f"ws://127.0.0.1:{server.socket.getsockname()[1]}/stream"
            source_capture=LoopbackCapture()
            source_capture.receive(url)
            assert not source_capture.complete and len(source_capture.record.packets)==2
            source_capture.receive(url)
            assert source_capture.complete and source_capture.record.packets==packets
            assert source_capture.record.counters["repeated"]==0 and offsets==[0,2]
            with pytest.raises(ValueError,match="本机采集桥"):
                LoopbackCapture().receive("ws://192.0.2.10:5000")
        finally:
            server.shutdown()
            thread.join(timeout=2)
