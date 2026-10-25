"""Interactive local ML page checks; never access an AI provider."""

from contextlib import ExitStack
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import rail_vehicle.db as db
from rail_vehicle.demo_data import ml_demo_samples
from rail_vehicle.ml_anomaly import MLUnavailableError, STATE_KEY
from rail_vehicle.pages import ml_detection, vehicles


def ml_app():
    from rail_vehicle.pages.ml_detection import render
    render()


class MLPageTests(unittest.TestCase):
    def test_missing_dependency_keeps_page_usable(self):
        with patch.object(ml_detection, "estimator_class", side_effect=MLUnavailableError()):
            app = AppTest.from_function(ml_app).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("依赖尚未安装" in item.value for item in app.warning))
        self.assertEqual(len(app.button), 0)


@unittest.skipUnless(importlib.util.find_spec("sklearn"), "Install optional .[ml] dependencies")
class MLExperimentPageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(ml_detection, "list_vehicles", return_value=[]))

    def demo(self):
        app = AppTest.from_function(ml_app).run()
        app.radio(key="ml_source").set_value("内置虚构实验").run()
        self.assertEqual(len(app.exception), 0)
        return app

    def test_model_runs_only_on_click_and_success_is_visible(self):
        with patch.object(ml_detection, "detect_ml_anomalies", wraps=ml_detection.detect_ml_anomalies) as detect:
            app = self.demo()
            detect.assert_not_called()
            app.button(key="ml_train").click().run()
            detect.assert_called_once()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.metric[0].value, "160")
            self.assertEqual(app.metric[2].value, "5")
            self.assertGreater(int(app.metric[1].value), 5)
            app.run()
            detect.assert_called_once()

    def test_changed_parameters_invalidate_result_without_retraining(self):
        app = self.demo()
        app.button(key="ml_train").click().run()
        self.assertIn(STATE_KEY, app.session_state)
        app.slider(key="ml_contamination").set_value(0.1).run()
        self.assertNotIn(STATE_KEY, app.session_state)
        self.assertEqual(len(app.metric), 0)
        app.button(key="ml_train").click().run()
        app.number_input(key="ml_rule_threshold").set_value(2.0).run()
        self.assertNotIn(STATE_KEY, app.session_state)

    def test_failed_model_does_not_display_output_or_raw_error(self):
        app = self.demo()
        with patch.object(ml_detection, "detect_ml_anomalies", side_effect=ValueError("PRIVATE_MODEL_DETAIL")):
            app.button(key="ml_train").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 0)
        self.assertNotIn(STATE_KEY, app.session_state)
        self.assertNotIn("PRIVATE_MODEL_DETAIL", " ".join(item.value for item in app.error))

    def test_no_vehicle_and_single_batch_give_clear_guidance(self):
        app = AppTest.from_function(ml_app).run()
        self.assertTrue(any("登记车辆" in item.value for item in app.info))
        with patch.object(ml_detection, "list_vehicles", return_value=[dict(id=1, vehicle_code="DEMO-001")]), \
             patch.object(ml_detection, "list_vehicle_batches", return_value=[]):
            app = AppTest.from_function(ml_app).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("两个历史批次" in item.value for item in app.info))

    def test_historical_reference_and_target_are_distinct_and_passed_correctly(self):
        reference, target = ml_demo_samples()
        vehicle = dict(id=1, vehicle_code="DEMO-001")
        batches = [dict(id=i, file_name=f"fictional-{i}.csv", sample_count=400 if i == 1 else 160) for i in (1, 2)]
        with patch.object(ml_detection, "list_vehicles", return_value=[vehicle]), \
             patch.object(ml_detection, "list_vehicle_batches", return_value=batches), \
             patch.object(ml_detection, "list_batch_samples", side_effect=lambda vid, bid: reference if bid == 1 else target), \
             patch.object(ml_detection, "detect_ml_anomalies", wraps=ml_detection.detect_ml_anomalies) as detect:
            app = AppTest.from_function(ml_app).run()
            self.assertEqual(app.selectbox(key="ml_target_1_1").value, 2)
            app.button(key="ml_train").click().run()
            self.assertEqual(detect.call_args.args, (reference, target))
            app.selectbox(key="ml_reference_1").set_value(2).run()
            self.assertNotIn(STATE_KEY, app.session_state)
            self.assertEqual(app.selectbox(key="ml_target_1_2").value, 1)

    def test_main_navigation_opens_optional_ml_page(self):
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        initialize = db.init_db
        self.stack.enter_context(patch.object(db, "init_db", lambda: initialize(Path(directory) / "nav.sqlite3")))
        self.stack.enter_context(patch.object(vehicles, "list_vehicles", return_value=[]))
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
        app.sidebar.radio[0].set_value("机器学习异常检测").run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(item.value == "机器学习异常检测" for item in app.header))
