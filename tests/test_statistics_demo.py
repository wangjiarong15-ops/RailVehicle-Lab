"""Fictional statistics workflow must stay independent of private data and AI."""

from contextlib import ExitStack
from datetime import time
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.demo_data import analysis_demo_data
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.pages import demo_center, run_analysis


class StatisticsDemoTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}))
        self.stack.enter_context(patch("rail_vehicle.db.init_db", side_effect=AssertionError("private database")))
        for name in ("list_vehicles", "list_vehicle_batches", "list_batch_samples", "get_vehicle"):
            self.stack.enter_context(patch.object(run_analysis, name, side_effect=AssertionError("private history")))
        self.stack.enter_context(patch.object(run_analysis.AIConfig, "from_sources", side_effect=AssertionError("no AI config")))
        self.ai = self.stack.enter_context(patch.object(run_analysis, "_render_ai_interpretation", side_effect=AssertionError("no AI")))

    def app(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        app.button(key="launch_demo_statistics").click().run()
        self.assertFalse(app.exception)
        return app

    def test_fictional_batches_are_independent_and_match_documented_events(self):
        vehicle, batches, data = analysis_demo_data()
        self.assertIn("虚构", vehicle["vehicle_type"])
        self.assertEqual([batch["sample_count"] for batch in batches], [120, 120])
        for rows in data.values():
            detected = detect_anomalies(rows, 1)
            self.assertEqual([event["sample_count"] for event in detected["events"]], [3, 2])
            self.assertEqual(len(detected["points"]), 5)
            self.assertNotIn("vehicle_id", rows[0])
        data[-1][0]["speed_kmh"] = -1
        self.assertGreater(analysis_demo_data()[2][-1][0]["speed_kmh"], 0)

    def test_public_entry_waits_for_explicit_calculation_without_private_reads(self):
        with patch.object(run_analysis, "analyze_run_data", wraps=run_analysis.analyze_run_data) as calculate:
            app = self.app()
            self.assertEqual(app.sidebar.radio(key="navigation_page").value, "统计与异常演示")
            self.assertEqual(len(app.metric), 0)
            calculate.assert_not_called()
            app.button(key="statistics_demo_calculate").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(calculate.called)
            self.assertTrue(any("120" in item.value for item in app.markdown))
            self.assertEqual(len(app.get("plotly_chart")), 4)
            self.assertFalse(any("生成 AI" in button.label for button in app.button))
            self.ai.assert_not_called()

    def test_threshold_changes_existing_rule_metrics_and_removes_event_review(self):
        app = self.app()
        app.button(key="statistics_demo_calculate").click().run()
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["数据异常点"], "5")
        self.assertEqual(metrics["统计异常区段"], "2")
        app.number_input(key="demo_analysis_threshold").set_value(10.).run()
        self.assertFalse(app.exception)
        self.assertEqual({metric.label: metric.value for metric in app.metric}["数据异常点"], "0")
        self.assertEqual(len(app.get("plotly_chart")), 3)

    def test_timerange_recalculates_and_empty_range_preserves_private_cache(self):
        app = self.app()
        app.session_state["ai_single_batch_result"] = {"private": "preserve"}
        app.session_state["analysis_report_pdf"] = b"PRIVATE PDF"
        app.button(key="statistics_demo_calculate").click().run()
        app.time_input(key="analysis_end_time_-1_-1").set_value(time(9, 30, 10)).run()
        self.assertTrue(any("11" in item.value for item in app.markdown if "分析样本数" in item.value))
        self.assertEqual({metric.label: metric.value for metric in app.metric}["数据异常点"], "0")
        app.time_input(key="analysis_start_time_-1_-1").set_value(time(10)).run()
        self.assertTrue(any("开始时间不能晚于" in warning.value for warning in app.warning))
        self.assertEqual(app.session_state["ai_single_batch_result"], {"private": "preserve"})
        self.assertEqual(app.session_state["analysis_report_pdf"], b"PRIVATE PDF")
        self.assertEqual(len(app.get("download_button")), 0)

    def test_fictional_pdf_is_generated_and_batch_change_invalidates_download(self):
        with patch.object(run_analysis, "build_analysis_report_data", wraps=run_analysis.build_analysis_report_data) as build:
            app = self.app()
            app.session_state["ai_single_batch_result"] = {"status": "available", "analysis": "must not enter demo"}
            app.button(key="statistics_demo_calculate").click().run()
            app.button(key="demo_generate_analysis_report").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(app.session_state["demo_analysis_report_pdf"].startswith(b"%PDF"))
            self.assertIsNone(build.call_args.kwargs["ai_result"])
            self.assertIn("虚构", build.call_args.kwargs["vehicle"]["vehicle_type"])
            self.assertIn("非实测", build.call_args.kwargs["batch"]["file_name"])
            app.selectbox(key="demo_dynamics_batch_id").select(-2).run()
            self.assertFalse(app.exception)
            self.assertNotIn("demo_analysis_report_pdf", app.session_state)
            self.assertEqual(len(app.get("download_button")), 0)

    def test_relaunch_resets_only_fictional_parameters_and_results(self):
        state = dict(statistics_demo_started=True, demo_analysis_report_pdf=b"OLD",
                     demo_dynamics_batch_id=-2)
        state["analysis_start_time_-1_-1"] = "old demo range"
        state["analysis_start_time_1_2"] = "private range"
        state["analysis_report_pdf"] = b"PRIVATE"
        with patch.object(demo_center.st, "session_state", state):
            demo_center.launch_demo("statistics")
        self.assertNotIn("statistics_demo_started", state)
        self.assertNotIn("demo_dynamics_batch_id", state)
        self.assertNotIn("analysis_start_time_-1_-1", state)
        self.assertEqual(state["analysis_start_time_1_2"], "private range")
        self.assertEqual(state["analysis_report_pdf"], b"PRIVATE")
        self.assertEqual(state["demo_analysis_threshold"], 1.)
