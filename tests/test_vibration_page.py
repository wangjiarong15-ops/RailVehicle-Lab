"""Interactive exploratory vibration page checks, without AI calls or user data."""

from contextlib import ExitStack
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import rail_vehicle.db as db
from rail_vehicle.demo_data import vibration_demo_samples
from rail_vehicle.pages import vehicles, vibration_analysis
from rail_vehicle.vibration import STATE_KEY, VibrationUnavailableError


def vibration_app():
    from rail_vehicle.pages.vibration_analysis import render
    render()


class VibrationDependencyPageTests(unittest.TestCase):
    def test_uninstalled_optional_dependency_disables_only_this_page(self):
        with patch.object(vibration_analysis, "signal_libraries", side_effect=VibrationUnavailableError()):
            app = AppTest.from_function(vibration_app).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("依赖尚未安装" in item.value for item in app.warning))
        self.assertEqual(len(app.button), 0)


@unittest.skipUnless(importlib.util.find_spec("scipy"), "Install optional .[signal] dependencies")
class VibrationPageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(vibration_analysis, "list_vehicles", return_value=[]))

    def demo(self):
        app = AppTest.from_function(vibration_app).run()
        app.radio(key="vibration_source").set_value("内置高频虚构信号").run()
        self.assertEqual(len(app.exception), 0)
        return app

    def test_demo_is_manual_and_shows_frequency_and_rolling_results(self):
        with patch.object(vibration_analysis, "analyze_vibration", wraps=vibration_analysis.analyze_vibration) as analyze:
            app = self.demo()
            analyze.assert_not_called()
            app.button(key="vibration_calculate").click().run()
            analyze.assert_called_once()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.metric[0].value, "3000")
            self.assertEqual(app.metric[2].value, "100.00")
            self.assertEqual(app.metric[3].value, "可计算")
            self.assertAlmostEqual(float(app.metric[4].value), 1.5, delta=0.1)
            self.assertAlmostEqual(float(app.metric[5].value), 6, delta=0.1)
            self.assertEqual(len(app.get("plotly_chart")), 2)

    def test_changed_band_and_time_range_remove_old_results(self):
        app = self.demo()
        app.button(key="vibration_calculate").click().run()
        app.number_input(key="vibration_band_high").set_value(15.0).run()
        self.assertNotIn(STATE_KEY, app.session_state)
        self.assertEqual(len(app.metric), 0)
        app.button(key="vibration_calculate").click().run()
        app.slider[0].set_value((0.0, 9.0)).run()
        self.assertNotIn(STATE_KEY, app.session_state)
        app.button(key="vibration_calculate").click().run()
        self.assertEqual(app.metric[3].value, "条件不足")
        self.assertEqual(len(app.get("plotly_chart")), 0)

    def test_historical_low_rate_keeps_metrics_and_explains_spectral_gate(self):
        samples = vibration_demo_samples()[::100]
        with patch.object(vibration_analysis, "list_vehicles", return_value=[dict(id=1, vehicle_code="DEMO-001")]), \
             patch.object(vibration_analysis, "list_vehicle_batches", return_value=[dict(id=1, file_name="fictional.csv", sample_count=30)]), \
             patch.object(vibration_analysis, "list_batch_samples", return_value=samples):
            app = AppTest.from_function(vibration_app).run()
            app.button(key="vibration_calculate").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.metric[2].value, "1.00")
        self.assertEqual(app.metric[3].value, "条件不足")
        self.assertTrue(any("奈奎斯特" in item.value for item in app.warning))
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertGreater(len(app.dataframe), 0)

    def test_missing_data_and_unexpected_errors_remain_safe(self):
        app = AppTest.from_function(vibration_app).run()
        self.assertTrue(any("登记车辆" in item.value for item in app.info))
        app = self.demo()
        with patch.object(vibration_analysis, "analyze_vibration", side_effect=RuntimeError("PRIVATE_COMPUTE_DETAIL")):
            app.button(key="vibration_calculate").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 0)
        self.assertNotIn("PRIVATE_COMPUTE_DETAIL", " ".join(item.value for item in app.error))
        self.assertNotIn(STATE_KEY, app.session_state)

    def test_navigation_opens_vibration_page_without_affecting_other_pages(self):
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        initialize = db.init_db
        self.stack.enter_context(patch.object(db, "init_db", lambda: initialize(Path(directory) / "nav.sqlite3")))
        self.stack.enter_context(patch.object(vehicles, "list_vehicles", return_value=[]))
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
        app.sidebar.radio[0].set_value("振动与平稳性分析").run()
        self.assertEqual(len(app.exception), 0)
        app.sidebar.radio[0].set_value("车辆参数").run()
        self.assertEqual(len(app.exception), 0)
