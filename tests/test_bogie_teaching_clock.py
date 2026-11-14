import json
from math import tau
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from rail_vehicle.bogie_replay import ILLUSTRATION_PERIOD, advance_teaching, control_teaching, illustration_phase
from rail_vehicle.demo_data import DEMO_BATCH_IDS
from rail_vehicle.replay import ReplayState


def test_pause_after_cycle_boundary_keeps_wrapped_pose_and_resumes_there():
    playing = ReplayState(position=ILLUSTRATION_PERIOD - .15, playing=True, last_tick=10.)
    paused = control_teaching(playing, "pause", 10.25)
    assert paused.position == pytest.approx(.1)
    assert illustration_phase(paused.position) == pytest.approx(tau * .1 / ILLUSTRATION_PERIOD)
    assert not paused.playing and paused.last_tick is None

    resumed = control_teaching(paused, "play", 20.)
    assert resumed.playing and resumed.last_tick == 20.
    assert advance_teaching(resumed, 20.25).position == pytest.approx(.35)


def test_rate_change_across_boundary_continues_using_new_rate():
    playing = ReplayState(position=ILLUSTRATION_PERIOD - .05, playing=True, rate=.5, last_tick=10.)
    faster = control_teaching(playing, "rate", 10.2, rate=2.)
    assert faster.position == pytest.approx(.05)
    assert faster.playing and faster.last_tick == 10.2
    assert advance_teaching(faster, 10.45).position == pytest.approx(.55)
    assert control_teaching(faster, "reset", 10.45) == ReplayState(rate=2.)


def test_workbench_holds_neutral_pose_inside_gap_but_not_at_observations(monkeypatch):
    monkeypatch.setenv("RAIL_PUBLIC_DEMO", "true")
    rows = [dict(timestamp=f"2026-01-01T00:00:{second:02d}Z", speed_kmh=20,
                 lateral_accel=2 if second == 2 else 0, vertical_accel=0)
            for second in (0, 1, 2, 15, 16)]
    with patch("rail_vehicle.pages.bogie.analysis_demo_data", return_value=(None, None, {DEMO_BATCH_IDS[0]: rows})):
        app = AppTest.from_string("from rail_vehicle.pages.bogie import render; render()").run()
        app.radio(key="bogie_source").set_value("内置虚构演示").run()
        timeline = app.slider(key="bogie_seek")

        timeline.set_value(2.).run()
        observed = json.loads(app.get("component_instance")[0].proto.json_args)
        assert observed["mode"] == "横向振动"
        assert observed["phase"] == pytest.approx(illustration_phase(2.))
        assert observed["hold"] is False and observed["periodic"] is False

        app.slider(key="bogie_seek").set_value(5.).run()
        gap = json.loads(app.get("component_instance")[0].proto.json_args)
        assert not app.exception
        assert gap["phase"] == 0. and gap["hold"] is True and gap["periodic"] is False
        assert gap["highlight"] == []
