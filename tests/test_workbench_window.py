"""Projected history reads must agree with the complete read and exact replay rows."""

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from rail_vehicle import db, run_data
from rail_vehicle.demo_data import analysis_demo_data
from rail_vehicle.pages import run_analysis
from rail_vehicle.pages.data_workflow import ANALYSIS_REPLAY_SCOPE
from rail_vehicle.replay_evidence import ReplayEvidenceScope
from rail_vehicle.time_filter import filter_samples_by_time_range
from rail_vehicle.vehicle_data import add_vehicle, list_vehicles


START = datetime(2026, 10, 8, 9, 30, 1, tzinfo=timezone.utc)
END = START + timedelta(microseconds=999_999)


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "fictional-workbench.sqlite3"
    db.init_db(path)
    vehicle = add_vehicle(analysis_demo_data()[0], path)
    stamps = (
        ("2026-10-08 11:30:01+02:00", 71),
        ("2026-10-08 09:30:01+00:00", 72),
        ("2026-10-08 09:30:01+00:00", 73),
        ("2026-10-08 09:30:01.999999", 76),
        ("2026-10-08 09:30:02", 77),
        ("2026-10-08 09:30:00", 70),
        ("2026-10-08 09:30:10", 78),
    )
    with db.get_connection(path) as connection:
        batch = connection.execute("INSERT INTO import_batches (file_name, row_count) VALUES (?, ?)",
                                   ("fictional-window.csv", len(stamps))).lastrowid
        connection.executemany("""INSERT INTO run_samples
            (batch_id, vehicle_id, timestamp, speed_kmh, lateral_accel, vertical_accel)
            VALUES (?, ?, ?, ?, 0, 0)""", [(batch, vehicle, stamp, speed) for stamp, speed in stamps])
    return path, vehicle, batch


def test_window_matches_full_filter_at_offsets_duplicates_and_boundaries(history):
    path, vehicle, batch = history
    window = run_data.list_batch_sample_window(vehicle, batch, START, END, path)
    complete = run_data.list_batch_samples(vehicle, batch, path)
    assert window.samples == filter_samples_by_time_range(complete, START, END)
    assert [row["speed_kmh"] for row in window.samples] == [72, 73, 71, 76]
    assert window.bounds == (START - timedelta(seconds=1), START + timedelta(seconds=9))
    empty = run_data.list_batch_sample_window(vehicle, batch, END + timedelta(days=1), END + timedelta(days=2), path)
    assert empty.samples == [] and empty.bounds == window.bounds


def test_dense_or_malformed_batch_requests_the_complete_reader(history):
    path, vehicle, batch = history
    with patch.object(run_data, "MAX_PROJECTED_WINDOW_ROWS", 2):
        assert run_data.list_batch_sample_window(vehicle, batch, START, END, path) is None
    with db.get_connection(path) as connection:
        connection.execute("UPDATE run_samples SET timestamp = ? WHERE speed_kmh = ?",
                           ("0001-01-01T00:00:00+14:00", 78))
    assert run_data.list_batch_sample_window(vehicle, batch, START, END, path) is None
    assert run_data.list_batch_samples(vehicle, batch, path)[-1]["timestamp"] == "0001-01-01T00:00:00+14:00"


def test_workbench_uses_projection_for_time_window_but_preserves_exact_replay_rows(history, monkeypatch):
    path, vehicle, batch = history
    init_db = db.init_db
    monkeypatch.setenv("RAIL_PUBLIC_DEMO", "false")
    monkeypatch.setattr(db, "init_db", lambda: init_db(path))
    monkeypatch.setattr(run_analysis, "list_vehicles", lambda: list_vehicles(path))
    monkeypatch.setattr(run_analysis, "list_vehicle_batches", lambda vid: run_data.list_vehicle_batches(vid, path))
    monkeypatch.setattr(run_analysis, "_MIN_WINDOW_BATCH_ROWS", 1)
    with (
        patch.object(run_analysis, "list_batch_samples",
                     side_effect=lambda vid, bid: run_data.list_batch_samples(vid, bid, path)) as full,
        patch.object(run_analysis, "list_batch_sample_window",
                     side_effect=lambda vid, bid, start, end: run_data.list_batch_sample_window(vid, bid, start, end, path)) as projected,
    ):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
        assert not app.exception and full.called
        app.time_input(key=f"analysis_start_time_{vehicle}_{batch}").set_value(time(9, 30, 1)).run()
        full.reset_mock()
        app.time_input(key=f"analysis_end_time_{vehicle}_{batch}").set_value(time(9, 30, 1)).run()
        assert not app.exception
        full.assert_not_called()
        projected.assert_called_once_with(vehicle, batch, START, END)
        assert any("4 条" in item.value for item in app.markdown if "分析样本数" in item.value)

        app.session_state[ANALYSIS_REPLAY_SCOPE] = ((vehicle, batch), ReplayEvidenceScope(2, 2, 7, START, START))
        projected.reset_mock()
        app.run()
        assert not app.exception
        projected.assert_not_called()
        full.assert_called_once_with(vehicle, batch)
        assert any("1 条" in item.value for item in app.markdown if "分析样本数" in item.value)
        assert {metric.label: metric.value for metric in app.metric}["平均速度（km/h）"] == "73.000"
