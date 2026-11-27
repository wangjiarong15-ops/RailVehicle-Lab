from datetime import datetime, timedelta, timezone

from rail_vehicle import db
from rail_vehicle.analysis_history import record_batch_analysis, saved_scope, source_kind
from rail_vehicle.anomaly import detect_anomalies, detect_anomalies_with_anchors
from rail_vehicle.dashboard import load_dashboard
from rail_vehicle.dashboard_chart import dashboard_chart
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.replay_evidence import ReplayEvidenceScope
from rail_vehicle.run_data import import_run_data, list_batch_samples, validate_run_csv


def test_exact_marker_rows_with_duplicate_times_sign_changes_and_gap():
    rows = [{"timestamp": stamp, "lateral_accel": lateral, "vertical_accel": vertical,
             "speed_kmh": 52., "csv_line": 2+i, "sample_id": 10+i}
            for i, (stamp, lateral, vertical) in enumerate([
                ("2026-01-01T00:01:00Z", -4., 0.), ("2026-01-01T00:00:00Z", 0., -2.),
                ("2026-01-01T00:00:00Z", 2., 0.), ("2026-01-01T00:01:01Z", 1., 0.)])]
    result = analyze_run_data(rows, 1.)
    anomalies, anchors = detect_anomalies_with_anchors(result["samples"], 1., include_rows=True)
    plain = detect_anomalies(rows, 1.)
    assert len(anchors) == len(plain["events"]) == 2
    assert [item["sample_count"] for item in plain["events"]] == [2, 1]
    assert plain["events"][0]["maximum_signed_value"] == -4.
    assert plain["events"][0]["duration_seconds"] == 60
    figure = dashboard_chart(result, anomalies, selected_event=1)
    assert list(figure.data[2].y) == [2., -4.]
    assert "CSV 第 4 行" in figure.data[2].text[0]
    assert "CSV 第 2 行" in figure.data[2].text[1]
    assert "CSV 第 3 行" in figure.data[4].text[0]
    assert sum(shape.fillcolor == "#CF8C39" for shape in figure.layout.shapes) == 3


def test_dense_display_keeps_exact_anomaly_and_common_indices():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{"timestamp": (start+timedelta(milliseconds=i)).isoformat(),
             "speed_kmh": float(i % 100), "vertical_accel": -.1,
             "lateral_accel": -2. if i == 12345 else .25, "csv_line": i+2} for i in range(30000)]
    result = analyze_run_data(rows, 1)
    anomalies, _ = detect_anomalies_with_anchors(rows, 1, include_rows=True)
    figure = dashboard_chart(result, anomalies)
    assert all(len(trace.x) <= 1200 for trace in figure.data)
    assert list(figure.data[0].x) == list(figure.data[1].x) == list(figure.data[3].x)
    assert figure.data[2].text[0].endswith("CSV 第 12347 行")
    assert list(figure.data[2].y) == [-2.]
    assert not figure.data[4].y


def test_history_survives_new_connection_with_exact_scope_and_batch_isolation(tmp_path):
    path = tmp_path/"history.sqlite3"
    db.init_db(path)
    with db.get_connection(path) as con:
        con.execute("INSERT INTO vehicles(vehicle_code) VALUES ('D')")
    csv = ("timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
           "2026-01-01T00:00:00Z,D,60,0,0\n"
           "2026-01-01T00:00:00Z,D,62,2,0\n")
    validated = validate_run_csv(csv, [{"id": 1, "vehicle_code": "D"}])
    first = import_run_data("first.csv", validated, path, source_kind="test")
    second = import_run_data("second.csv", validated, path)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scope = ReplayEvidenceScope(1, 1, 2, stamp, stamp)
    for _ in range(2):
        record_batch_analysis(1, first, validated.source_sha256, "test", 1.2, scope, path)
    db.init_db(path)
    dashboard = load_dashboard(path)
    assert dashboard["preview"]["batch_id"] == second
    assert dashboard["preview"]["samples"] == list_batch_samples(1, second, path)
    assert source_kind(dashboard["preview"]["source_kind"]) == "unknown"
    [saved] = dashboard["recent"]
    assert saved["batch_id"] == first and saved["source_kind"] == "test"
    assert saved["threshold"] == 1.2 and saved_scope(saved) == scope
    with db.get_connection(path) as con:
        con.execute("UPDATE import_batches SET source_sha256 = NULL WHERE id=?", (first,))
    assert load_dashboard(path)["recent"] == []


def test_legacy_dashboard_keeps_unknown_provenance_without_migration(tmp_path):
    path = tmp_path/"legacy.sqlite3"
    with db.get_connection(path) as con:
        con.executescript("""CREATE TABLE vehicles (id INTEGER, vehicle_code TEXT);
            CREATE TABLE import_batches (id INTEGER, file_name TEXT, row_count INTEGER, imported_at TEXT);
            CREATE TABLE run_samples (id INTEGER, batch_id INTEGER, vehicle_id INTEGER,
              timestamp TEXT, speed_kmh REAL, lateral_accel REAL, vertical_accel REAL);
            INSERT INTO vehicles VALUES (1, 'DEMO');
            INSERT INTO import_batches VALUES (1, 'legacy.csv', 1, '2026-01-01');
            INSERT INTO run_samples VALUES (10,1,1,'2026-01-01T00:00:00Z',20,0,0);""")
    before = path.read_bytes()
    dashboard = load_dashboard(path)
    assert dashboard["recent"] == []
    assert dashboard["preview"]["samples"][0]["sample_id"] == 10
    assert dashboard["preview"]["samples"][0]["csv_line"] is None
    assert source_kind(dashboard["preview"].get("source_kind")) == "unknown"
    assert path.read_bytes() == before
    assert source_kind("measurement") == "measurement"
    assert source_kind("unknown", '{"manifest":{"source_kind":"synthetic"}}') == "synthetic"
