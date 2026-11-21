from copy import deepcopy
from hashlib import sha256
import json

import pytest

from rail_vehicle.calibration_check import check_reference
from rail_vehicle.db import get_connection, init_db
from rail_vehicle.run_data import import_run_data, list_batch_samples, validate_run_csv
from rail_vehicle.sensor_store import revise, sensor_snapshot
from scripts.make_calibration_example import REFERENCE_NAME, SOURCE_NAME, VEHICLE_CODE, example_files


CHANNEL = "lateral_accel"


@pytest.fixture
def paired(tmp_path):
    database = tmp_path / "calibration-test.db"
    init_db(database)
    with get_connection(database) as connection:
        connection.execute("INSERT INTO vehicles(vehicle_code) VALUES(?)", (VEHICLE_CODE,))
    files = example_files()
    batch = import_run_data(SOURCE_NAME, validate_run_csv(files[SOURCE_NAME], [
        {"vehicle_code": VEHICLE_CODE, "id": 1}
    ]), database)
    metadata = {
        "part_id": "side_beam_l", "position_frame": "model", "position_m": [0, -1.16, 0.98],
        "mount_status": "verified", "origin": "illustration", "source": "虚构登记单",
        "calibration": "verified", "axis": "+Y", "unit": "m/s²", "gain": 2, "bias": 0.1,
        "sensor_to_vehicle": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
        "vehicle_to_model": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "clock_status": "verified", "time_basis": "device_offset", "clock_offset_s": -0.04,
    }
    revise(1, batch, CHANNEL, "CAL-SYNTH-01", metadata, expected_version=0, db_path=database)
    return sensor_snapshot(1, batch, database), list_batch_samples(1, batch, database), files


def run(paired, **overrides):
    snapshot, samples, files = paired
    kwargs = dict(reference_record="虚构独立激励记录", acceleration_limit=0.01, time_limit_ms=1, min_excitation=1)
    kwargs.update(overrides)
    return check_reference(snapshot, CHANNEL, samples, files[REFERENCE_NAME], **kwargs)


def test_import_to_reference_report_with_signed_axis_gain_and_clock(paired):
    snapshot, samples, files = paired
    before = deepcopy((snapshot, samples))
    packet = run(paired)
    assert packet["status"] == "within_limits"
    assert packet["configuration"]["version"] == 1
    assert packet["registration"]["direction"] == [-1, 0, 0]
    assert packet["source_sha256"] == sha256(files[SOURCE_NAME]).hexdigest()
    assert packet["reference_sha256"] == sha256(files[REFERENCE_NAME]).hexdigest()
    assert [p["csv_line"] for p in packet["pairs"]] == [2, 3, 4, 5, 6]
    assert [p["expected_m_s2"] for p in packet["pairs"]] == [2, -1.5, 0, -2, 1]
    assert packet["metrics"]["rms_error_m_s2"] == pytest.approx(0)
    assert packet["metrics"]["max_time_error_ms"] == 0
    assert (snapshot, samples) == before
    json.dumps(packet, allow_nan=False)


@pytest.mark.parametrize(("changes", "status", "accel", "clock"), [
    ({"axis": "-Y"}, "outside_limits", 4, 0),
    ({"gain": 1}, "outside_limits", 1.05, 0),
    ({"clock_offset_s": 0}, "outside_limits", 0, 40),
    ({"axis": "+Z"}, "insufficient_excitation", 2, 0),
])
def test_detects_wrong_sign_gain_clock_or_missing_excitation(paired, changes, status, accel, clock):
    paired[0]["channels"][CHANNEL]["metadata"].update(changes)
    packet = run(paired)
    assert packet["status"] == status
    assert packet["metrics"]["max_acceleration_error_m_s2"] == pytest.approx(accel)
    assert packet["metrics"]["max_time_error_ms"] == pytest.approx(clock)


def test_does_not_silently_drop_a_point_outside_analysis_window(paired):
    paired[1].pop()
    with pytest.raises(ValueError, match="分析窗口"):
        run(paired)


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda s: s.replace(b",2,", b",3,", 1), "重复"),
    (lambda s: s.replace(b"08:00:00+00:00", b"08:00:01+00:00", 1), "重复"),
    (lambda s: s.replace(b"+00:00", b"", 1), "带时区"),
    (lambda s: s.replace(b",-2.0,", b",nan,", 1), "有限"),
    (lambda s: s[:s.index(b"\n")] + b"\n", "3 个"),
    (lambda s: s.replace(s.splitlines()[1].split(b",")[0], b"0" * 64, 1), "SHA-256"),
])
def test_reference_validation(paired, mutation, message):
    files = paired[2]
    files[REFERENCE_NAME] = mutation(files[REFERENCE_NAME])
    with pytest.raises(ValueError, match=message):
        run(paired)


def test_unverified_clock_and_missing_source_are_not_evidence(paired):
    paired[0]["channels"][CHANNEL]["metadata"]["clock_status"] = "unknown"
    with pytest.raises(ValueError, match="时间基准"):
        run(paired)
    paired[0]["source_sha256"] = None
    with pytest.raises(ValueError, match="摘要"):
        run(paired)


def test_duplicate_observation_in_reference_pairing_is_ambiguous(paired):
    paired[1][1]["timestamp"] = paired[1][0]["timestamp"]
    with pytest.raises(ValueError, match="重复的对齐时刻"):
        run(paired)


def test_duplicate_observation_line_cannot_replace_missing_reference(paired):
    paired[1][1]["csv_line"] = paired[1][0]["csv_line"]
    with pytest.raises(ValueError, match="重复的原始 CSV 行"):
        run(paired)
