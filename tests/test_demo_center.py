"""Session-only guided demos; no database writes, provider calls or computation."""

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import rail_vehicle.db as db
from rail_vehicle.pages import cockpit, demo_center, ml_detection, vehicles, vibration_analysis
from rail_vehicle.vibration import STATE_KEY as VIBRATION_STATE_KEY


class DemoCenterTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        initialize = db.init_db
        self.stack.enter_context(patch.object(db, "init_db", lambda: initialize(Path(directory)/"demo.sqlite3")))
        self.stack.enter_context(patch.object(vehicles, "list_vehicles", return_value=[]))

    def app(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
        app.sidebar.radio(key="navigation_page").set_value("演示中心").run()
        self.assertEqual(len(app.exception), 0)
        return app

    def test_portal_is_read_only_and_offers_four_explicit_launches(self):
        with patch.object(ml_detection, "detect_ml_anomalies") as detect, \
             patch.object(vibration_analysis, "analyze_vibration") as analyze:
            app = self.app()
        self.assertEqual(len(app.button), 4)
        self.assertTrue(any("不自动计算" in item.value for item in app.info))
        detect.assert_not_called()
        analyze.assert_not_called()

    def test_cockpit_callback_selects_fictional_data_without_starting_playback(self):
        with patch.object(cockpit, "list_vehicles", side_effect=AssertionError("must not read history")):
            app = self.app()
            app.button(key="launch_demo_cockpit").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.sidebar.radio(key="navigation_page").value, "动态运行驾驶舱")
        self.assertEqual(app.radio(key="cockpit_source").value, "内置虚构演示")
        self.assertFalse(app.session_state[cockpit.STATE_KEY].playing)

    def test_experiment_callbacks_select_defaults_without_computing(self):
        for kind, page, source_key, label, compute_name in (
            ("ml", ml_detection, "ml_source", "内置虚构实验", "detect_ml_anomalies"),
            ("vibration", vibration_analysis, "vibration_source", "内置高频虚构信号", "analyze_vibration")):
            with self.subTest(kind=kind), patch.object(page, compute_name) as compute:
                app = self.app()
                if app.button(key=f"launch_demo_{kind}").disabled:
                    self.assertFalse(demo_center.importlib.util.find_spec("sklearn" if kind == "ml" else "scipy"))
                    continue
                app.button(key=f"launch_demo_{kind}").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.radio(key=source_key).value, label)
                self.assertEqual(len(app.metric), 0)
                compute.assert_not_called()

    def test_fleet_callback_opens_fictional_comparison_without_history(self):
        app = self.app()
        app.button(key="launch_demo_fleet").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.radio(key="fleet_source").value, "内置虚构对比")
        self.assertEqual(len(app.dataframe), 0)

    def test_vibration_launch_clears_only_its_demo_results_and_demo_range(self):
        state = {VIBRATION_STATE_KEY: {"pdf": b"OLD"}, "experiment_vibration_ai": {"status": "available"},
                 "vibration_range_{'source': 'demo'}": (0, 2),
                 "vibration_range_{'source': 'history', 'vehicle': 1}": (2, 5),
                 "unrelated_work": "preserve", "AI_ENABLED": False}
        with patch.object(demo_center.st, "session_state", state):
            demo_center.launch_demo("vibration")
        self.assertNotIn(VIBRATION_STATE_KEY, state)
        self.assertNotIn("experiment_vibration_ai", state)
        self.assertNotIn("vibration_range_{'source': 'demo'}", state)
        self.assertEqual(state["vibration_range_{'source': 'history', 'vehicle': 1}"], (2, 5))
        self.assertEqual(state["unrelated_work"], "preserve")
        self.assertFalse(state["AI_ENABLED"])
        self.assertEqual(state["vibration_band_high"], 20.0)

    def test_missing_optional_dependencies_disable_only_relevant_cards(self):
        find_spec = demo_center.importlib.util.find_spec
        with patch.object(demo_center.importlib.util, "find_spec", side_effect=lambda name: None if name in ("sklearn", "scipy") else find_spec(name)):
            app = self.app()
        self.assertTrue(app.button(key="launch_demo_ml").disabled)
        self.assertTrue(app.button(key="launch_demo_vibration").disabled)
        self.assertFalse(app.button(key="launch_demo_cockpit").disabled)
        self.assertFalse(app.button(key="launch_demo_fleet").disabled)
