"""Same-axis events at the same UTC instant must not jump to the first match."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from rail_vehicle import db
from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.center_state import STATE_KEY
from rail_vehicle.anomaly import detect_anomalies, detect_anomalies_with_anchors
from rail_vehicle.demo_data import analysis_demo_data
from rail_vehicle.event_anchor import resolve_event_row
from rail_vehicle.pages import ai_center, bogie, run_analysis
from rail_vehicle.replay import prepare_replay


def fictional_rows():
    stamps = ("09:30:00+00:00", "09:30:01+00:00", "11:30:01+02:00", "09:30:01+00:00", "09:30:02+00:00")
    return [dict(timestamp=f"2026-10-08T{stamp}", speed_kmh=speed, lateral_accel=lateral, vertical_accel=0.)
            for stamp, speed, lateral in zip(stamps, (70, 78, 79, 83, 84), (.1, 2., .2, -3., .1))]


def test_exact_anchor_preserves_detection_and_rejects_ambiguous_or_changed_rows():
    rows = fictional_rows()
    original, anchors = detect_anomalies_with_anchors(rows, 1.)
    assert original == detect_anomalies(rows, 1.)
    assert len(original["events"]) == 2 and original["events"][0]["start_timestamp"] == original["events"][1]["start_timestamp"]
    track = prepare_replay(rows)
    timestamp = original["events"][1]["start_timestamp"]
    assert resolve_event_row(track, timestamp, "lateral", 1., anchor=anchors[0]) == 1
    assert resolve_event_row(track, timestamp, "lateral", 1., anchor=anchors[1]) == 3
    assert resolve_event_row(track, timestamp, "lateral", 1.) is None

    _, scoped = detect_anomalies_with_anchors(rows[2:4], 1.)
    assert resolve_event_row(track, timestamp, "lateral", 1., row_span=(3, 4), anchor=scoped[0]) == 3
    assert resolve_event_row(track, timestamp, "lateral", 1., row_span=(3, 6), anchor=scoped[0]) is None
    assert resolve_event_row(track, timestamp, "lateral", 1., anchor=scoped[0]) is None
    rows[3]["speed_kmh"] += 10
    assert resolve_event_row(prepare_replay(rows), timestamp, "lateral", 1., anchor=anchors[1]) is None


def test_workbench_and_offline_ai_evidence_jump_to_the_same_second_event(tmp_path, monkeypatch):
    rows = fictional_rows()
    vehicle = dict(analysis_demo_data()[0], id=1)
    batches = [dict(id=1, file_name="fictional-duplicates.csv", imported_at="2026-10-08", sample_count=len(rows))]
    initialize = db.init_db
    monkeypatch.setenv("RAIL_PUBLIC_DEMO", "false")
    monkeypatch.setattr(db, "init_db", lambda: initialize(tmp_path / "fictional.sqlite3"))
    monkeypatch.setattr(ai_center.AIConfig, "from_sources", lambda: AIConfig(enabled=False))
    for page in (run_analysis, bogie):
        monkeypatch.setattr(page, "list_vehicles", lambda: [vehicle])
        monkeypatch.setattr(page, "list_vehicle_batches", lambda _: batches)
        monkeypatch.setattr(page, "list_batch_samples", lambda *_: rows)

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
    assert not app.exception
    app.selectbox(key="anomaly_review_event").set_value(1).run()
    app.button(key="run_analysis_bogie_jump").click().run()
    assert not app.exception and app.session_state[bogie.FRAME_KEY] == 3

    app.button(key="bogie_return_analysis").click().run()
    app.button(key="ai_center_link_single").click().run()
    evidence = "event.lateral.002"
    app.selectbox(key="ai_center_evidence_single").set_value(evidence).run()
    context = app.session_state[STATE_KEY]["single"]
    assert evidence in context["replay_anchors"]
    assert all("fingerprint" not in event and "occurrence" not in event for event in context["payload"]["anomaly_events"])
    app.button(key="ai_center_evidence_jump").click().run()
    assert not app.exception and app.session_state[bogie.FRAME_KEY] == 3
    app.button(key="bogie_return_evidence").click().run()
    assert app.selectbox(key="ai_center_evidence_single").value == evidence

    rows[3]["speed_kmh"] += 10
    app.button(key="ai_center_evidence_jump").click().run()
    assert not app.exception and app.session_state[bogie.FRAME_KEY] is None
    assert any("无法唯一定位" in warning.value for warning in app.warning)
