"""Public demonstration must neither access history nor create AI clients."""

import os
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.deployment import demo_source_options, public_demo_enabled
from rail_vehicle.ai.ui import request_ai_analysis
from rail_vehicle.pages import cockpit, ml_detection, vibration_analysis, fleet_comparison
from rail_vehicle.pages.experiment_ai import render_experiment_ai


class PublicDemoTests(unittest.TestCase):
    def test_default_full_mode_and_explicit_public_flag(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(public_demo_enabled())
            self.assertEqual(demo_source_options("history", "demo"), ["history", "demo"])
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}):
            self.assertEqual(demo_source_options("history", "demo"), ["demo"])

    def test_public_navigation_never_initializes_or_reads_database(self):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}))
            initialize = stack.enter_context(patch("rail_vehicle.db.init_db", side_effect=AssertionError("no database")))
            for module in (cockpit, ml_detection, vibration_analysis, fleet_comparison):
                for name in ("list_vehicles", "list_vehicle_batches", "list_batch_samples"):
                    stack.enter_context(patch.object(module, name, side_effect=AssertionError("no history")))
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run()
            self.assertFalse(app.exception)
            navigation = app.sidebar.radio(key="navigation_page")
            self.assertEqual(navigation.value, "演示中心")
            self.assertNotIn("车辆参数", navigation.options)
            self.assertNotIn("运行分析", navigation.options)
            for name, page, key in (
                ("cockpit", "动态运行驾驶舱", "cockpit_source"),
                ("ml", "机器学习异常检测", "ml_source"),
                ("vibration", "振动与平稳性分析", "vibration_source"),
                ("fleet", "多车辆运行对比", "fleet_source"),
            ):
                with self.subTest(page=page):
                    app.sidebar.radio(key="navigation_page").set_value("演示中心").run()
                    if app.button(key=f"launch_demo_{name}").disabled:
                        continue
                    app.button(key=f"launch_demo_{name}").click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(len(app.radio(key=key).options), 1)
            initialize.assert_not_called()

    def test_service_blocks_even_enabled_config_before_provider_creation(self):
        factory = Mock(side_effect=AssertionError("must not create provider"))
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}):
            result = request_ai_analysis({}, Mock(enabled=True), has_data=True, provider_factory=factory)
        self.assertEqual(result["code"], "public_demo")
        factory.assert_not_called()

    def test_public_experiments_compute_and_export_without_config_or_database(self):
        from rail_vehicle.ml_anomaly import STATE_KEY as ML_KEY
        from rail_vehicle.vibration import STATE_KEY as VIBRATION_KEY
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}), \
             patch("rail_vehicle.db.init_db", side_effect=AssertionError("no database")), \
             patch("rail_vehicle.pages.experiment_ai.AIConfig.from_sources", side_effect=AssertionError("no config")):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"), default_timeout=30).run()
            for kind, action, state_key in (("ml", "ml_train", ML_KEY), ("vibration", "vibration_calculate", VIBRATION_KEY)):
                with self.subTest(kind=kind):
                    app.sidebar.radio(key="navigation_page").set_value("演示中心").run()
                    if app.button(key=f"launch_demo_{kind}").disabled:
                        continue
                    app.button(key=f"launch_demo_{kind}").click().run()
                    app.button(key=action).click().run()
                    self.assertFalse(app.exception)
                    self.assertGreater(len(app.metric), 0)
                    self.assertFalse(any("生成实验 AI" in button.label for button in app.button))
                    app.button(key=f"{kind}_report_generate").click().run()
                    self.assertFalse(app.exception)
                    self.assertTrue(app.session_state[state_key]["pdf"].startswith(b"%PDF"))

    def test_experiment_clears_ai_without_reading_config(self):
        state = {"experiment_ml_ai": {"status": "available"}}
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}), \
             patch("rail_vehicle.pages.experiment_ai.st.session_state", state), \
             patch("rail_vehicle.pages.experiment_ai.st.info"), \
             patch("rail_vehicle.pages.experiment_ai.AIConfig.from_sources", side_effect=AssertionError("no config")):
            self.assertEqual(render_experiment_ai({}, "fingerprint", {}, "ml"), (None, None))
        self.assertNotIn("experiment_ml_ai", state)
