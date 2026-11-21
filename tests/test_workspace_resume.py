import json
from unittest.mock import patch

import pytest

from rail_vehicle.ui import AI, ANALYSIS, ANOMALIES, BOGIE, DEMO_DATA, LINKED, REPORT
from rail_vehicle.workspace_resume import (
    BATCH_HANDOFF, BOGIE_EVENT, WORKBENCH_RETURN, _capture, decode_checkpoint,
)
from rail_vehicle import workspace_resume


@pytest.fixture
def checkpoint():
    return {"v": 1, "origin": ANOMALIES, "page": AI, "demo": False,
            "vehicle": 3, "batch": 4, "start": "2026-10-09T09:30:00+00:00",
            "end": "2026-10-09T09:31:15.999999+00:00", "threshold": 1.1,
            "event": [1, "c" * 64]}


def test_browser_checkpoint_only_returns_allowed_fields(checkpoint):
    raw = {**checkpoint, "context": {"samples": [1]}, "key": "not-a-real-key", "pdf": "fixture"}
    assert decode_checkpoint(json.dumps(raw), public=False) == checkpoint
    assert decode_checkpoint(json.dumps(raw), public=True) is None
    demo = {**checkpoint, "demo": True, "batch": -2}
    assert decode_checkpoint(json.dumps(demo), public=True) == demo


@pytest.mark.parametrize("edits", [
    {"v": True}, {"v": 2}, {"origin": AI}, {"origin": DEMO_DATA}, {"page": "未知菜单"},
    {"page": ANALYSIS}, {"demo": "false"}, {"vehicle": True}, {"batch": 0}, {"batch": "4"},
    {"start": "2026-10-09T09:30:00"}, {"start": "2026-10-09T09:30:00+08:00"},
    {"end": "2024-01-01T00:00:00Z"}, {"threshold": -0.1}, {"threshold": float("inf")},
    {"threshold": float("nan")}, {"event": [-1, "c" * 64]}, {"event": [1, "BAD"]},
    {"event": {"a": 1, "b": 2}}, {"event": [1, "c" * 64, 2]},
])
def test_browser_checkpoint_rejects_invalid_selectors(checkpoint, edits):
    assert decode_checkpoint(json.dumps({**checkpoint, **edits}), public=False) is None


def test_row_scope_has_matching_time_endpoints_and_batch_bounds(checkpoint):
    start, end = checkpoint["start"], checkpoint["end"]
    saved = {**checkpoint, "page": BOGIE, "event_jump": True, "rows": [2, 8, 20, start, end]}
    assert decode_checkpoint(json.dumps(saved), public=False) == saved
    assert decode_checkpoint(json.dumps({**saved, "rows": [2, 20, 20, start, end]}), public=False) is None
    assert decode_checkpoint(json.dumps({**saved, "rows": [2, 8, 20, start, start]}), public=False) is None
    assert decode_checkpoint(json.dumps({**saved, "demo": True}), public=False) is None
    saved.pop("event")
    assert decode_checkpoint(json.dumps(saved), public=False) is None


def test_unrelated_ai_evidence_and_bogie_event_return_to_origin(checkpoint):
    from rail_vehicle.pages.bogie import HISTORY

    event = {"axis": "vertical", "event": "computed"}
    state = {BATCH_HANDOFF: {"evidence_id": "event.vertical.001", "event_target": event},
             WORKBENCH_RETURN: (ANOMALIES, {}), "ai_center_evidence_single": "event.vertical.001",
             "bogie_selection": (HISTORY, 3, 4), "bogie_track": object(), "bogie_threshold": 1.1,
             BOGIE_EVENT: event}
    with patch("rail_vehicle.workspace_resume.st.session_state", state):
        assert _capture(AI, checkpoint) == checkpoint
        assert _capture(BOGIE, checkpoint)["event_jump"] is True
        state["ai_center_evidence_single"] = "event.lateral.001"
        assert _capture(AI, checkpoint)["page"] == ANOMALIES
        state[BOGIE_EVENT] = {**event, "axis": "lateral"}
        assert _capture(BOGIE, checkpoint)["page"] == ANOMALIES
        state["bogie_selection"] = (HISTORY, 3, 5)
        assert _capture(BOGIE, checkpoint) is None


def test_linked_workspace_preserves_scoped_event_checkpoint(checkpoint):
    linked = dict(checkpoint, page=LINKED, event_jump=True)
    assert decode_checkpoint(json.dumps(linked), public=False) == linked
    assert decode_checkpoint(json.dumps(linked), public=True) is None


def test_report_only_carries_prior_event_for_the_same_scope(checkpoint):
    selection = {"selected_start": checkpoint["start"], "selected_end": checkpoint["end"]}
    target = {"vehicle_id": checkpoint["vehicle"], "batch_id": checkpoint["batch"],
              "threshold": checkpoint["threshold"], "fictional": False}
    state = {BATCH_HANDOFF: {"evidence_selection": selection, "target": target}}
    with patch("rail_vehicle.workspace_resume.st.session_state", state):
        assert _capture(REPORT, checkpoint)["event"] == checkpoint["event"]
        target["threshold"] = 2.0
        assert "event" not in _capture(REPORT, checkpoint)


def test_stale_warning_survives_reruns_on_the_origin_page(checkpoint):
    state = {workspace_resume._FORWARD: checkpoint}
    current = dict(checkpoint, page=ANOMALIES, event=[1, "e" * 64])
    with (patch.object(workspace_resume.st, "session_state", state),
          patch.object(workspace_resume.st, "info") as info,
          patch.object(workspace_resume, "_capture", return_value=current),
          patch.object(workspace_resume, "_mount"),
          patch.object(workspace_resume, "select_workspace_page") as forward):
        workspace_resume.finish_workspace(None, ANOMALIES, public=False)
        forward.assert_not_called()
        workspace_resume.render_resume_notice(ANOMALIES)
        assert info.call_count == 2
        workspace_resume.render_resume_notice(REPORT)
        workspace_resume.render_resume_notice(ANOMALIES)
        assert info.call_count == 2
