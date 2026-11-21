from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import sqlite3

import pytest

from rail_vehicle import db
from rail_vehicle.copilot.context import Dataset, csv_datasets
from rail_vehicle.copilot.planning import local_plan
from rail_vehicle.copilot.tools import Work, execute
from rail_vehicle.replay import prepare_replay
from rail_vehicle.run_data import import_run_data, list_batch_samples, validate_run_csv
from rail_vehicle.sensor_evidence import observation, project_frame_response, viewer_sensors
from rail_vehicle.sensor_mapping import acceleration_si, aligned_time, registration, rotation, validate_metadata
from rail_vehicle.sensor_store import active_channels, revisions, revise, sensor_snapshot
from rail_vehicle.suspension import SuspensionCase, simulate


I = [[1,0,0],[0,1,0],[0,0,1]]
R = [[0,-1,0],[1,0,0],[0,0,1]]
META = dict(part_id="axlebox_a_l", origin="illustration", source="虚构台架 A",
            mount_status="verified", position_frame="vehicle", position_m=[1,0,0],
            vehicle_origin_model_m=[-1,0,1], vehicle_to_model=R, sensor_to_vehicle=R,
            axis="-X", calibration="verified", gain=2, bias=.5, unit="m/s²",
            rate_hz=1, time_basis="device_offset", clock_status="verified", clock_offset_s=-.25)
CSV = ('timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel,notes\r\n'
       '2026-01-01T00:00:01,A,20,3,0,"two\r\nlines"\r\n'
       '\r\n2026-01-01T00:00:00,B,15,0,0,\r\n'
       '2026-01-01T00:00:00,A,20,-2,0,\r\n2026-01-01T00:00:00,A,20,0,0,\r\n').encode()


@pytest.fixture
def history(tmp_path):
    path = tmp_path/"isolated.db"
    db.init_db(path)
    with db.get_connection(path) as con:
        con.executemany("INSERT INTO vehicles(vehicle_code) VALUES(?)",[("A",),("B",)])
    batch = import_run_data("fiction.csv", validate_run_csv(CSV,[dict(vehicle_code="A",id=1),dict(vehicle_code="B",id=2)]),path)
    return path, batch


def test_old_schema_migrates_without_inventing_csv_line_or_mount(tmp_path):
    path = tmp_path/"old.db"
    with sqlite3.connect(path) as con:
        con.executescript("""CREATE TABLE vehicles(id INTEGER PRIMARY KEY,vehicle_code TEXT);
            CREATE TABLE import_batches(id INTEGER PRIMARY KEY,file_name TEXT,row_count INTEGER,imported_at TEXT);
            CREATE TABLE run_samples(id INTEGER PRIMARY KEY,batch_id INT,vehicle_id INT,timestamp TEXT,
                speed_kmh REAL,lateral_accel REAL,vertical_accel REAL);
            INSERT INTO vehicles VALUES(1,'old'); INSERT INTO import_batches VALUES(1,'old.csv',1,'old');
            INSERT INTO run_samples VALUES(1,1,1,'2020-01-01 00:00:00',10,1,2);""")
    db.init_db(path)
    db.init_db(path)
    snap = sensor_snapshot(1,1,path)
    assert snap["channels"] == {} and snap["source_sha256"] is None
    row = list_batch_samples(1,1,path)[0]
    ev = observation(snap,"vertical_accel",row,1)
    assert ev["raw_si"] == 2 and ev["exceeded"]
    assert ev["database_sample_id"] == 1
    assert ev["csv_line"] is ev["synchronized_utc"] is ev["part_id"] is ev["version"] is None


def test_declared_frames_signed_axis_units_and_time():
    info = registration(validate_metadata(META))
    assert info == dict(position=[-1,1,1], vehicle_direction=[0,-1,0], direction=[1,0,0])
    assert acceleration_si(1,"g",2,-1) == pytest.approx(9.80665)
    assert acceleration_si(1000,"mm/s²",1,0) == 1
    assert aligned_time("2026-01-01 08:00:00+08:00",META) == "2025-12-31T23:59:59.750000+00:00"
    assert aligned_time("2026-01-01 00:00:00",{}) is None
    assert aligned_time("2026-01-01 00:00:00",dict(META,clock_status="unverified")) is None
    assert aligned_time("2026-01-01 00:00:00",{"time_basis":"device_offset"}) is None
    assert registration(dict(META,calibration="unverified"))["direction"] is None
    assert registration(dict(META,mount_status="unknown"))["position"] is None


@pytest.mark.parametrize("matrix", [[[1,0,0],[0,2,0],[0,0,1]], [[-1,0,0],[0,1,0],[0,0,1]],
                                    [[1,0,0]], [[True,0,0],[0,1,0],[0,0,1]]])
def test_invalid_rotations_fail_closed(matrix):
    with pytest.raises(ValueError):
        rotation(matrix)


@pytest.mark.parametrize("overrides", [dict(axis="±X"),dict(gain=0),dict(rate_hz=-1),dict(unit="g"),
                                       dict(part_id="fake"),dict(position_m=[0,0,float("inf")]),
                                       dict(source=""),dict(time_basis="utc"),dict(sensor_to_vehicle=None)])
def test_invalid_verified_metadata(overrides):
    with pytest.raises(ValueError):
        validate_metadata(dict(META,**overrides))


def test_revision_history_ownership_and_optimistic_concurrency(history):
    path, bid = history
    field = "lateral_accel"
    def edit(serial, m, v, vehicle=1, channel=field):
        return revise(vehicle,bid,channel,serial,m,expected_version=v,db_path=path)
    assert edit("S1",{},0) == 1
    with pytest.raises(ValueError, match="另一辆"):
        edit("S1",{},0,vehicle=2)
    with pytest.raises(ValueError, match="编号"):
        edit("S 1",{},0,channel="vertical_accel")
    def compete(i):
        try:
            return edit("S1",dict(META,source=str(i)),1)
        except ValueError as e:
            assert "其他会话" in str(e)
            return None
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(compete, range(2))).count(2) == 1
    with pytest.raises(ValueError,match="同一测量轴"):
        edit("S1",META,0,channel="vertical_accel")
    with pytest.raises(ValueError,match="不匹配"):
        edit("S2",{},0,vehicle=3)
    assert edit("S1",None,2) == 3
    assert not active_channels(sensor_snapshot(1,bid,path))
    assert edit("S2",{},3) == 4
    assert [r["version"] for r in revisions(1,bid,field,path)] == [4,3,2,1]
    assert revisions(1,bid,field,path)[1]["metadata"] is None


def test_csv_to_copilot_and_model_snapshot(history):
    path,bid = history
    channel = "lateral_accel"
    revise(1,bid,channel,"S-A",META,expected_version=0,db_path=path)
    snap = sensor_snapshot(1,bid,path)
    rows = list_batch_samples(1,bid,path)
    assert [s["csv_line"] for s in rows] == [6,7,2]
    assert list_batch_samples(2,bid,path)[0]["csv_line"] == 5
    assert snap["source_sha256"] == sha256(CSV).hexdigest()
    sample = prepare_replay(rows).samples[0]
    e = observation(snap,channel,sample,1)
    assert (e["csv_line"],e["raw_si"],e["calibrated_axis_si"],e["origin"]) == (6,-2,-3.5,"illustration")
    assert observation(snap,channel,sample,1,gap=True)["exceeded"] is None
    marker = viewer_sensors(snap,channel)["channels"][0]
    assert marker["position"] == [-1,1,1] and marker["direction"] == [1,0,0] and marker["illustration"]
    def dataset(snapshot):
        return Dataset("history",dict(id=1,vehicle_code="A",vehicle_type="虚构"),dict(id=bid,file_name="fiction.csv"),
                       rows,sensors=snapshot)
    work = Work(dataset(snap))
    calls = list(execute(work,local_plan("核查传感器坐标标定与横向异常")))
    assert [s["tool"] for s in calls if s["status"]=="done"] == ["sensors","events"]
    assert calls[1]["result"].rows[0]["模型系正方向"] == [1,0,0]
    assert len(calls[-1]["result"].events) == 2
    stats = list(execute(work,[dict(tool="statistics",args={"axis":"lateral"})]))[-1]["result"]
    assert stats.provenance["exceeded_csv_lines"] == [6,2]
    assert stats.provenance["database_sample_ids"] == [3,4,1]
    assert stats.provenance["configuration"]["version"] == 1
    assert "S-A" not in json.dumps(work.anonymous_payload,ensure_ascii=False)
    revise(1,bid,channel,"S-A",{},expected_version=1,db_path=path)
    assert dataset(sensor_snapshot(1,bid,path)).identity != work.data.identity
    assert stats.provenance["configuration"]["metadata"] == validate_metadata(META)
    upload = csv_datasets(CSV,"fiction.csv",1)["A"]
    assert upload.sensors == {} and upload.batch["source_sha256"] == snap["source_sha256"]


def test_validation_error_uses_same_physical_line_as_evidence():
    bad = CSV.replace(b"20,-2,0,", b"20,bad,0,")
    result = validate_run_csv(bad, [dict(vehicle_code="A", id=1), dict(vehicle_code="B", id=2)])
    assert result.errors == ["第 6 行：lateral_accel 必须是数字。"]


def test_read_only_inventory_never_creates_database(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        sensor_snapshot(1, 1, path)
    assert not path.exists()


def test_projection_uses_solver_relative_time_and_revision(history):
    path, batch = history
    channel = "vertical_accel"
    metadata = dict(META, part_id="side_beam_l", axis="-Z")
    revise(1, batch, channel, "S-Z", metadata, expected_version=0, db_path=path)
    snapshot = sensor_snapshot(1, batch, path)
    response = simulate(SuspensionCase("rough"))
    p = project_frame_response(snapshot, channel, 1, response)
    assert p["parameters"] == response["parameters"] and p["time_basis"] == "relative_seconds"
    assert p["axis_accel_m_s2"] == [-v for v in response["channels"]["accel_m_s2"]]
    assert max(p["axis_accel_m_s2"]) > 0 and p["configuration"]["metadata"]["origin"] == "illustration"
    for unsupported in ("axle_bump", "hunting", "brake", "curve"):
        with pytest.raises(ValueError, match="单自由度"):
            project_frame_response(snapshot, channel, 1, simulate(SuspensionCase(unsupported)))
    revise(1, batch, channel, "S-Z", dict(metadata, axis="+X"), expected_version=1, db_path=path)
    snapshot = sensor_snapshot(1, batch, path)
    with pytest.raises(ValueError, match="版本"):
        project_frame_response(snapshot, channel, 1, response)
    assert not any(project_frame_response(snapshot, channel, 2, response)["axis_accel_m_s2"])
    revise(1, batch, channel, "S-Z", META, expected_version=2, db_path=path)
    with pytest.raises(ValueError, match="构架"):
        project_frame_response(sensor_snapshot(1, batch, path), channel, 3, response)
