"""Sampling review UI and per-vehicle CSV checks use actual current data."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from rail_vehicle.run_data import validate_run_csv


def quality_app():
    import streamlit as st
    from rail_vehicle.pages.sampling_quality import render_sampling_quality
    render_sampling_quality(st.session_state["rows"], key="qa")


def import_app():
    from io import BytesIO
    from unittest.mock import patch
    from rail_vehicle.pages import run_analysis
    upload = BytesIO(b"timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
                     b"2026-11-01T00:00:00Z,A,80,0,0\n2026-11-01T00:00:00Z,B,80,0,0")
    upload.name = "fictional.csv"
    with patch.object(run_analysis.st, "file_uploader", return_value=upload), \
         patch.object(run_analysis, "list_vehicles", return_value=[
             {"id": 1, "vehicle_code": "A"}, {"id": 2, "vehicle_code": "B"}]), \
         patch.object(run_analysis, "list_import_batches", return_value=[]), \
         patch.object(run_analysis, "import_run_data", side_effect=AssertionError("no automatic import")):
        run_analysis._render_import()


class SamplingQualityPageTests(unittest.TestCase):
    def test_import_ui_shows_per_vehicle_review_without_writing(self):
        app = AppTest.from_function(import_app).run()
        self.assertFalse(app.exception)
        table = app.dataframe[1].value
        self.assertEqual(list(table["重复时刻额外记录"]), [0, 0])
        self.assertEqual(list(table["提示"]), ["独立时刻不足", "独立时刻不足"])
        self.assertTrue(any("格式检查通过" in item.value for item in app.success))

    def test_real_analysis_quality_rule_preserves_statistics_ai_payload_and_invalidates_pdf(self):
        from tests.test_anomaly_review import analysis_app
        from rail_vehicle.pages import run_analysis
        with patch.object(run_analysis, "_render_ai_interpretation") as ai:
            app = AppTest.from_function(analysis_app).run()
            self.assertFalse(app.exception)
            context = deepcopy(ai.call_args.kwargs["payload"])
            app.button(key="generate_analysis_report").click().run()
            self.assertFalse(app.exception)
            pdf = app.session_state["analysis_report_pdf"]
            metrics = {metric.label: metric.value for metric in app.metric}
            app.number_input(key="history_sampling_1_1_expected_interval").set_value(.1).run()
            self.assertFalse(app.exception)
            updated = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["长采样间隔"], "1")
            self.assertEqual(updated["长采样间隔"], "4")
            self.assertEqual(metrics["数据异常点"], updated["数据异常点"])
            self.assertEqual(metrics["平均速度（km/h）"], updated["平均速度（km/h）"])
            self.assertEqual(ai.call_args.kwargs["payload"], context)
            self.assertNotIn("analysis_report_pdf", app.session_state)
            self.assertEqual(len(app.get("download_button")), 0)
            app.button(key="generate_analysis_report").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state["analysis_report_pdf"].startswith(b"%PDF"))
            self.assertNotEqual(app.session_state["analysis_report_pdf"], pdf)
            app.number_input(key="history_sampling_1_1_gap_multiplier").set_value(100.).run()
            self.assertNotIn("analysis_report_pdf", app.session_state)
            self.assertEqual(len(app.get("download_button")), 0)

    def test_rule_changes_recompute_gaps_and_keep_other_results(self):
        app = AppTest.from_function(quality_app)
        app.session_state["rows"] = [{"timestamp": f"2026-11-01T00:00:{second:02}Z"}
                                     for second in [0, 10, 20]]
        app.session_state["analysis_report_pdf"] = b"previous pdf"
        app.session_state["ai_single_batch_result"] = {"unchanged": True}
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual({m.label: m.value for m in app.metric}["长采样间隔"], "0")
        app.number_input(key="qa_expected_interval").set_value(1.).run()
        self.assertFalse(app.exception)
        self.assertEqual({m.label: m.value for m in app.metric}["长采样间隔"], "2")
        self.assertEqual(len(app.dataframe[0].value), 2)
        app.number_input(key="qa_gap_multiplier").set_value(10.).run()
        self.assertEqual({m.label: m.value for m in app.metric}["长采样间隔"], "0")
        self.assertEqual(len(app.dataframe), 0)
        self.assertEqual(app.session_state["analysis_report_pdf"], b"previous pdf")
        self.assertEqual(app.session_state["ai_single_batch_result"], {"unchanged": True})

    def test_changed_range_removes_old_gaps_and_single_instant_is_explicit(self):
        app = AppTest.from_function(quality_app)
        app.session_state["rows"] = [{"timestamp": "2026-11-01T00:00:00Z"}]
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("不足两个" in item.value for item in app.info))
        app.session_state["rows"] = [{"timestamp": f"2026-11-01T00:00:{second:02}Z"}
                                     for second in [0, 1, 2, 20]]
        app.run()
        self.assertEqual(len(app.dataframe), 1)
        app.session_state["rows"] = app.session_state["rows"][:3]
        app.run()
        self.assertEqual(len(app.dataframe), 0)
        self.assertEqual({m.label: m.value for m in app.metric}["长采样间隔"], "0")

    def test_csv_separates_vehicles_and_keeps_original_order(self):
        header = "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel"
        rows = [f"2026-11-01T00:00:{second:02}Z,{code},80,0,0"
                for second, code in [(0, "A"), (0, "B"), (2, "A"), (1, "B"), (1, "A")]]
        result = validate_run_csv("\n".join([header, *rows]),
                                  [{"id": 1, "vehicle_code": "A"}, {"id": 2, "vehicle_code": "B"}])
        self.assertTrue(result.is_valid)
        self.assertEqual(result.sampling_by_vehicle["A"]["duplicate_timestamp_rows"], 0)
        self.assertEqual(result.sampling_by_vehicle["B"]["duplicate_timestamp_rows"], 0)
        self.assertEqual(result.sampling_by_vehicle["A"]["out_of_order_pairs"], 1)
        self.assertEqual(result.sampling_by_vehicle["B"]["out_of_order_pairs"], 0)
        self.assertEqual(len(result.rows), 5)

    def test_invalid_csv_does_not_offer_partial_quality_results(self):
        result = validate_run_csv("timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
                                  "2026-11-01T00:00:00Z,A,80,0,0\nbad,A,80,0,0",
                                  [{"id": 1, "vehicle_code": "A"}])
        self.assertFalse(result.is_valid)
        self.assertEqual(result.sampling_by_vehicle, {})
