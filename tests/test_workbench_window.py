"""Projected history reads must agree with the complete read and exact replay rows."""

from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from rail_vehicle import db, run_data
from rail_vehicle.demo_data import analysis_demo_data
from rail_vehicle.pages import bogie, run_analysis
from rail_vehicle.pages.data_workflow import ANALYSIS_REPLAY_SCOPE, BATCH_HANDOFF, REPORT_COMPARISON
from rail_vehicle.replay import prepare_replay
from rail_vehicle.replay_evidence import ReplayEvidenceScope, ReportComparison
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
    assert (window.first_row, window.batch_count) == (2, 7)
    empty = run_data.list_batch_sample_window(vehicle, batch, END + timedelta(days=1), END + timedelta(days=2), path)
    assert empty.samples == [] and empty.bounds == window.bounds
    assert (empty.first_row, empty.batch_count) == (8, 7)


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
    with db.get_connection(path) as connection:
        connection.execute("UPDATE run_samples SET lateral_accel = -2 WHERE speed_kmh = 73")
    track = prepare_replay(run_data.list_batch_samples(vehicle, batch, path))
    comparison = ReportComparison.capture(track, 2, 1, vehicle_id=vehicle, batch_id=batch,
                                          fictional=False, threshold=1, speed_tolerance=5)
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
        app.sidebar.button(key="nav_运行分析").click().run()
        assert not app.exception and full.called
        app.time_input(key=f"analysis_start_time_{vehicle}_{batch}").set_value(time(9, 30, 1)).run()
        full.reset_mock()
        app.time_input(key=f"analysis_end_time_{vehicle}_{batch}").set_value(time(9, 30, 1)).run()
        assert not app.exception
        full.assert_not_called()
        projected.assert_called_once_with(vehicle, batch, START, END)
        assert any("4 条" in item.value for item in app.markdown if "分析样本数" in item.value)
        assert app.session_state[BATCH_HANDOFF]["target"]["scope"] == ReplayEvidenceScope(1, 4, 7, START, END)

        app.session_state[REPORT_COMPARISON] = comparison
        projected.reset_mock()
        app.run()
        assert not app.exception
        full.assert_not_called()
        projected.assert_called_once_with(vehicle, batch, START, END)
        assert any("异常第 3 条，参照第 2 条" in item.value for item in app.success)

        app.time_input(key=f"analysis_start_time_{vehicle}_{batch}").set_value(time(9, 30, 2))
        app.time_input(key=f"analysis_end_time_{vehicle}_{batch}").set_value(time(9, 30, 2)).run()
        assert not app.exception
        full.assert_not_called()
        assert REPORT_COMPARISON not in app.session_state
        assert any("不再同时匹配" in item.value for item in app.info)

        app.session_state[ANALYSIS_REPLAY_SCOPE] = ((vehicle, batch), ReplayEvidenceScope(2, 2, 7, START, START))
        projected.reset_mock()
        app.run()
        assert not app.exception
        projected.assert_not_called()
        full.assert_called_once_with(vehicle, batch)
        assert any("1 条" in item.value for item in app.markdown if "分析样本数" in item.value)
        assert {metric.label: metric.value for metric in app.metric}["平均速度（km/h）"] == "73.000"


def test_analysis_slice_checks_row_range_and_timestamps(history):
    path, vehicle, batch = history
    track = prepare_replay(run_data.list_batch_samples(vehicle, batch, path))
    fragment = track.samples[1:5]
    scope = ReplayEvidenceScope.from_analysis_slice(fragment, first_row=1, batch_count=7)
    assert (scope.first_row, scope.last_row, scope.count) == (1, 4, 4)
    assert scope.matches_track(track)
    assert not scope.matches_track(prepare_replay(track.samples[:-1]))
    assert ReplayEvidenceScope.from_analysis_slice(track.samples, first_row=0, batch_count=7) is None
    with pytest.raises(ValueError):
        ReplayEvidenceScope.from_analysis_slice(fragment, first_row=4, batch_count=7)


def test_same_time_window_round_trip_preserves_chart_evidence_and_report(history, monkeypatch):
    path, vehicle, batch = history
    with db.get_connection(path) as connection:
        connection.execute("UPDATE run_samples SET lateral_accel = speed_kmh - 70 WHERE speed_kmh IN (72, 73)")
    init_db = db.init_db
    monkeypatch.setenv("RAIL_PUBLIC_DEMO", "false")
    monkeypatch.setenv("AI_ENABLED", "false")
    monkeypatch.setenv("RAIL_PORTABLE_HOME", str(path.parent))
    monkeypatch.setattr(db, "init_db", lambda: init_db(path))
    for page in (bogie, run_analysis):
        monkeypatch.setattr(page, "list_vehicles", lambda: list_vehicles(path))
        monkeypatch.setattr(page, "list_vehicle_batches", lambda vid: run_data.list_vehicle_batches(vid, path))
        monkeypatch.setattr(page, "list_batch_samples", lambda vid, bid: run_data.list_batch_samples(vid, bid, path))
    get_vehicle = run_analysis.get_vehicle
    monkeypatch.setattr(run_analysis, "get_vehicle", lambda vid: get_vehicle(vid, path))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
    app.sidebar.button(key="nav_运行分析").click().run()
    app.button(key="analysis_replay_batch").click().run()
    app.number_input(key="bogie_loop_before").set_value(0.)
    app.number_input(key="bogie_loop_after").set_value(0.).run()
    app.button(key="bogie_start_loop").click().run()
    app.button(key="bogie_window_analysis").click().run()
    assert not app.exception
    assert any("第 2–3 条" in info.value for info in app.info)
    charts = {item.proto.id.rsplit("-", 1)[-1]: json.loads(item.proto.spec) for item in app.get("plotly_chart")}
    lateral = charts["chart_lateral_accel"]
    assert lateral["layout"]["xaxis"]["title"]["text"] == "批次样本序号"
    assert lateral["data"][0]["x"] == lateral["data"][1]["x"] == [2, 3]
    assert lateral["data"][0]["y"] == [2., 3.]

    with patch.object(run_analysis, "generate_analysis_report_pdf", wraps=run_analysis.generate_analysis_report_pdf) as report:
        app.sidebar.button(key="nav_分析报告").click().run()
        assert any("报告范围：批次第 2–3 条观测（按样本行）" in item.value for item in app.markdown)
        assert any("2 条观测 · 1 个规则异常区段" in item.value for item in app.markdown)
        assert any("异常规则：加速度绝对值大于 1 m/s²。" in item.value for item in app.caption)
        app.button(key="generate_analysis_report").click().run()
        payload = report.call_args.args[0]
        assert payload["replay_rows"] == (2, 3) and payload["sample_count"] == 2
        assert [sample["speed_kmh"] for sample in payload["samples"]] == [72, 73]
        assert app.session_state["analysis_report_pdf"].startswith(b"%PDF-")

    app.button(key="ai_center_link_single").click().run()
    assert any("当前证据范围：批次第 2–3 条" in item.value for item in app.caption)
    context = app.session_state["ai_center_contexts"]["single"]
    assert context["selection"]["row_span"] == [2, 3]
    assert context["payload"]["anomaly_events"][0]["sample_count"] == 2
    assert app.button(key="ai_center_generate").disabled
    next(button for button in app.button if button.label == "返回分析页面").click().run()

    app.button(key="analysis_replay_batch").click().run()
    loop = app.session_state[bogie.LOOP_KEY]["window"]
    assert (loop.first_row, loop.last_row) == (1, 2)
    assert app.session_state[bogie.FRAME_KEY] == 1 and not app.session_state[bogie.STATE_KEY].playing
    assert not app.exception

    app.button(key="bogie_return_analysis").click().run()
    with db.get_connection(path) as connection:
        connection.execute("DELETE FROM run_samples WHERE speed_kmh = 78")
    app.button(key="analysis_replay_batch").click().run()
    assert not app.exception
    assert bogie.LOOP_KEY not in app.session_state
    assert any("分析窗口与当前批次" in message.value for message in app.warning)
