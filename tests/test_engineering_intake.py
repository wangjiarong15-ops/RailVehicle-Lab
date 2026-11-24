from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from rail_vehicle.db import get_connection, init_db
from rail_vehicle.engineering_intake import assess_intake, configuration_review, load_manifest, report_provenance
from rail_vehicle.run_data import import_run_data, list_import_batches, validate_run_csv
from rail_vehicle.sensor_store import revise, sensor_snapshot


VEHICLE = {"id": 1, "vehicle_code": "CAL-DEMO-001"}
HEADER = "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"


@pytest.fixture
def intake_files():
    times = ("00:00:00Z", "00:00:00.2Z", "00:00:00.2Z", "00:00:05Z")
    source = (HEADER + "".join(f"2026-01-05T{stamp},CAL-DEMO-001,0,0.1,0.2\n" for stamp in times)).encode()
    example = Path(__file__).resolve().parents[1] / "docs" / "engineering-validation" / "metadata.example.json"
    manifest = load_manifest(example.read_bytes())
    manifest["source_sha256"] = sha256(source).hexdigest()
    return source, manifest


def test_persists_matched_declaration_and_quality_without_certifying(intake_files, tmp_path):
    source, manifest = intake_files
    database = tmp_path / "engineering.db"
    init_db(database)
    with get_connection(database) as con:
        con.execute("INSERT INTO vehicles(vehicle_code) VALUES (?)", (VEHICLE["vehicle_code"],))
    validation = validate_run_csv(source, [VEHICLE])
    intake = assess_intake(validation, manifest)
    quality = intake["quality"]
    assert (quality["baseline_seconds"], quality["short_interval_count"], quality["long_gap_count"],
            quality["duplicate_timestamp_rows"]) == (1, 1, 1, 1)
    assert intake["clock_drift_evidence"] == "insufficient"
    assert quality["duplicate_row_fraction"] == 0.25
    assert quality["median_implied_rate_hz"] == pytest.approx(0.4)
    assert quality["full_scale_candidate_counts"] == {"lateral_accel": None, "vertical_accel": None}
    with pytest.raises(ValueError, match="SHA-256"):
        import_run_data("wrong.csv", validation, database, engineering_manifest={**manifest, "source_sha256": "0" * 64})
    assert not list_import_batches(database)
    batch = import_run_data("fictional.csv", validation, database, engineering_manifest=manifest)
    legacy_batch = import_run_data("plain.csv", validation, database)
    snapshot = sensor_snapshot(1, batch, database)
    assert snapshot["engineering"] == intake
    assert sensor_snapshot(1, legacy_batch, database)["engineering"] is None
    report = report_provenance(snapshot)
    assert report["source"] == "虚构工程演练" and report["independent_measurement"] == "pending"

    binding = manifest["sensors"]["lateral_accel"]
    metadata = {
        **{key: binding[key] for key in ("part_id", "position_frame", "position_m", "axis")},
        "unit": "m/s²", "time_basis": "device_offset", "clock_offset_s": -.04, "rate_hz": 1,
        "gain": 2, "bias": 0.1, "origin": "illustration", "source": "虚构试验",
        "clock_status": "verified", "mount_status": "verified", "calibration": "verified",
        "sensor_to_vehicle": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    }
    revise(1, batch, "lateral_accel", binding["sensor_id"], metadata, expected_version=0, db_path=database)
    review = configuration_review(intake, sensor_snapshot(1, batch, database)["channels"])
    assert [(row["channel"], row["version"], row["status"]) for row in review] == [
        ("lateral_accel", 1, "matches"), ("vertical_accel", None, "missing")]
    revise(1, batch, "lateral_accel", binding["sensor_id"], {**metadata, "axis": "-Y"}, expected_version=1,
           db_path=database)
    [lateral, _] = report_provenance(sensor_snapshot(1, batch, database))["configurations"]
    assert (lateral["version"], lateral["status"], lateral["differences"]) == (2, "mismatch", ["axis"])


def test_missing_counts_cover_records_after_diagnostics_are_truncated():
    source = HEADER + "2026-01-05T00:00:00Z,CAL-DEMO-001,,,-1\n" * 80
    result = validate_run_csv(source, [VEHICLE])
    assert not result.is_valid
    assert result.missing_by_field == {"speed_kmh": 80, "lateral_accel": 80}
    assert len(result.errors) < 80


def test_engineering_requires_explicit_timezone_and_single_vehicle(intake_files):
    source, manifest = intake_files
    naive = source.replace(b"Z,", b",")
    result = validate_run_csv(naive, [VEHICLE])
    assert result.is_valid  # The existing import keeps supporting its declared default.
    with pytest.raises(ValueError, match="4 行.*时区"):
        assess_intake(result, {**manifest, "source_sha256": sha256(naive).hexdigest()})
    with pytest.raises(ValueError, match="单车"):
        assess_intake(validate_run_csv(source, [VEHICLE]), {**manifest, "vehicle_code": "OTHER"})


@pytest.mark.parametrize(("edits", "message"), [
    ({"sample_rate_hz": 0}, "采样频率"),
    ({"time_basis": "utc"}, "时间基准"),
    ({"units": {"lateral_accel": "g"}}, "CSV 单位"),
    ({"source_kind": "certified"}, "来源类型"),
    ({"sensors": {}}, "两|横向"),
])
def test_manifest_invalid_declarations(intake_files, edits, message):
    _, manifest = intake_files
    with pytest.raises(ValueError, match=message):
        load_manifest(json.dumps({**manifest, **edits}).encode())


def test_invalid_axis_and_duplicate_keys(intake_files):
    _, manifest = intake_files
    changed = deepcopy(manifest)
    changed["sensors"]["vertical_accel"]["axis"] = "Z"
    with pytest.raises(ValueError, match="方向无效"):
        load_manifest(json.dumps(changed).encode())
    with pytest.raises(ValueError, match="有效的 UTF-8"):
        load_manifest(b'{"schema": "first", "schema": "second"}')


def test_declared_scale_preserves_inclusive_boundary_and_unknown_channel(intake_files):
    source, manifest = intake_files
    source = source.replace(b"0.1,0.2", b"-0.1,0.2", 1)
    manifest["source_sha256"] = sha256(source).hexdigest()
    manifest["sensors"]["lateral_accel"]["full_scale_m_s2"] = 0.1
    parsed = load_manifest(json.dumps(manifest).encode())
    result = assess_intake(validate_run_csv(source, [VEHICLE]), parsed)
    assert result["quality"]["full_scale_candidate_counts"] == {"lateral_accel": 4, "vertical_accel": None}
    manifest["sensors"]["lateral_accel"]["full_scale_m_s2"] = 0
    with pytest.raises(ValueError):
        load_manifest(json.dumps(manifest).encode())
