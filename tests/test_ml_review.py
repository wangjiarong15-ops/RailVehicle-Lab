"""Deterministic sample windows and UI review without model/AI requests."""

from copy import deepcopy
import importlib.util
import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.ml_review import flagged_indices, sample_window
from rail_vehicle.pages.ml_review import review_chart
from rail_vehicle.pages import ml_detection


def records():
    return [dict(timestamp=f"2026-10-26T00:00:{second:02d}+00:00", speed_kmh=60+i,
                 lateral_accel=.1+i, vertical_accel=-.2-i, ml_score=.4+i/10,
                 category=category, ml_anomaly=category in {"both", "ml_only"},
                 rule_anomaly=category in {"both", "rule_only"})
            for i, (second, category) in enumerate(((0, "neither"), (1, "ml_only"), (2, "both"), (10, "rule_only")))]


def result(rows=None):
    rows = records() if rows is None else rows
    return dict(records=rows, score_threshold=.5, rule_threshold=1.)


def review_app():
    import streamlit as st
    from rail_vehicle.pages.ml_review import render_review
    render_review(st.session_state["result"], st.session_state["fingerprint"])


def ml_app():
    from rail_vehicle.pages.ml_detection import render
    render()


class MLReviewTests(unittest.TestCase):
    def test_category_filters_copy_existing_labels_only(self):
        rows = records()
        before = deepcopy(rows)
        self.assertEqual(flagged_indices(rows), [1, 2, 3])
        for category, expected in (("both", [2]), ("ml_only", [1]), ("rule_only", [3])):
            self.assertEqual(flagged_indices(rows, category), expected)
        self.assertEqual(rows, before)
        with self.assertRaises(ValueError):
            flagged_indices(rows, "fault")

    def test_window_is_inclusive_utc_and_preserves_sampling_gaps(self):
        rows = records()
        rows[1]["timestamp"] = "2026-10-26T08:00:01+08:00"
        window = sample_window(rows, 2, 2)
        self.assertEqual([row["relative_seconds"] for row in window["rows"]], [-2, -1, 0])
        self.assertEqual(sample_window(rows, 3, 10)["rows"][-1]["relative_seconds"], 0)
        self.assertEqual([row["relative_seconds"] for row in sample_window(rows, 3, 10)["rows"]], [-10, -9, -8, 0])

    def test_zero_window_keeps_duplicate_records_with_distinct_indices(self):
        rows = records()
        rows[1]["timestamp"] = rows[2]["timestamp"]
        window = sample_window(rows, 2, 0)
        self.assertEqual([row["source_index"] for row in window["rows"]], [1, 2])
        self.assertEqual(window["selected_index"], 2)

    def test_plot_sampling_is_bounded_and_keeps_selected_point(self):
        rows = [dict(records()[1], timestamp="2026-10-26T00:00:01Z") for _ in range(10_000)]
        window = sample_window(rows, 5555, 0, plot_limit=10)
        self.assertEqual(len(window["rows"]), 10_000)
        self.assertLessEqual(len(window["visible"]), 11)
        self.assertIn(5555, [row["source_index"] for row in window["visible"]])
        self.assertEqual(window["visible"][0]["source_index"], 0)
        self.assertEqual(window["visible"][-1]["source_index"], 9999)

    def test_invalid_selection_and_window_are_rejected(self):
        for index, seconds in ((-1, 1), (99, 1), (True, 1), (1, -1), (1, 61), (1, float("nan")), (1, True)):
            with self.subTest(index=index, seconds=seconds), self.assertRaises(ValueError):
                sample_window(records(), index, seconds)

    def test_chart_marks_exact_selected_point_and_keeps_strict_rule_thresholds(self):
        window = sample_window(records(), 2, 5)
        chart = review_chart(window, 1.)
        self.assertEqual(len(chart.data), 6)
        self.assertTrue(all(trace.mode == "markers" for trace in chart.data))
        for trace in chart.data[1::2]:
            self.assertEqual(list(trace.x), [0])
        self.assertEqual([shape.y0 for shape in chart.layout.shapes], [-1, 1, -1, 1])

    def app(self, rows=None):
        app = AppTest.from_function(review_app)
        app.session_state["result"] = result(rows)
        app.session_state["fingerprint"] = "first-input"
        return app.run()

    def test_page_selects_category_and_point_without_training_or_ai(self):
        with patch.object(ml_detection, "detect_ml_anomalies", side_effect=AssertionError("no training")), \
             patch("rail_vehicle.pages.experiment_ai.request_ai_analysis", side_effect=AssertionError("no AI")):
            app = self.app()
            app.selectbox(key="ml_review_category").set_value("rule_only").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="ml_review_sample").value, 3)
            app.slider(key="ml_review_seconds").set_value(0).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("窗口含 1 条" in item.value for item in app.caption))

    def test_empty_category_and_unflagged_result_have_no_review_chart(self):
        rows = [dict(row, category="neither") for row in records()]
        app = self.app(rows)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertTrue(any("没有标记样本" in item.value for item in app.info))

    def test_input_change_resets_selected_sample_without_reusing_old_index(self):
        app = self.app()
        app.selectbox(key="ml_review_sample").set_value(3).run()
        self.assertEqual(app.selectbox(key="ml_review_sample").value, 3)
        app.session_state["result"] = result(records()[:3])
        app.session_state["fingerprint"] = "second-input"
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="ml_review_sample").value, 1)

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "Install optional .[ml] dependencies")
    def test_review_controls_preserve_cached_model_and_existing_pdf(self):
        from rail_vehicle.ml_anomaly import STATE_KEY
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}), \
             patch.object(ml_detection, "detect_ml_anomalies", wraps=ml_detection.detect_ml_anomalies) as train:
            app = AppTest.from_function(ml_app)
            app.session_state["ml_source"] = "内置虚构实验"
            app.run()
            app.button(key="ml_train").click().run()
            app.button(key="ml_report_generate").click().run()
            original_pdf = app.session_state[STATE_KEY]["pdf"]
            original_records = deepcopy(app.session_state[STATE_KEY]["result"]["records"])
            app.slider(key="ml_review_seconds").set_value(2).run()
            app.selectbox(key="ml_review_category").set_value("both").run()
            self.assertFalse(app.exception)
            train.assert_called_once()
            self.assertEqual(app.session_state[STATE_KEY]["pdf"], original_pdf)
            self.assertEqual(app.session_state[STATE_KEY]["result"]["records"], original_records)
            self.assertEqual(len(app.get("download_button")), 1)
