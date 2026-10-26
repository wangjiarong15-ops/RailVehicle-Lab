"""Event drill-down uses existing detection without changing analysis facts."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.anomaly_review import event_window
from rail_vehicle.pages.anomaly_review import event_chart


def samples():
    return [dict(timestamp=f"2026-10-26T00:00:{t:02d}Z", speed_kmh=50 + t,
                 lateral_accel=value, vertical_accel=-value)
            for t, value in ((0, .1), (1, 2.), (2, -3.), (8, .1), (9, 4.))]


def review_app():
    import streamlit as st
    from rail_vehicle.pages.anomaly_review import render_event_review
    render_event_review(st.session_state["samples"], st.session_state["anomalies"], st.session_state["selection"])


def analysis_app():
    from unittest.mock import patch
    from rail_vehicle.pages import run_analysis
    from rail_vehicle.ai.config import AIConfig
    from tests.test_anomaly_review import samples
    vehicle = dict(id=1, vehicle_code="FICTIONAL", vehicle_type="虚构演示车辆", vehicle_length_m=20,
                   mass_kg=40000, axle_count=4, bogie_count=2, max_speed_kmh=120, notes="")
    batches = [dict(id=1, file_name="fictional.csv", imported_at="2026-10-26", sample_count=5)]
    with patch.object(run_analysis, "list_vehicles", return_value=[vehicle]), \
         patch.object(run_analysis, "get_vehicle", return_value=vehicle), \
         patch.object(run_analysis, "list_vehicle_batches", return_value=batches), \
         patch.object(run_analysis, "list_batch_samples", return_value=samples()), \
         patch.object(run_analysis.AIConfig, "from_sources", return_value=AIConfig(enabled=False)):
        run_analysis._render_dynamics_analysis()


class AnomalyReviewTests(unittest.TestCase):
    def test_closed_window_keeps_only_supplied_filtered_data(self):
        rows = samples()
        detected = detect_anomalies(rows, 1)
        window = event_window(rows, detected, 0, 1)
        self.assertEqual([row["relative_seconds"] for row in window["rows"]], [-1, 0, 1])
        self.assertEqual(window["duration"], 1)
        restricted = event_window(rows[1:], detected, 0, 1)
        self.assertEqual(len(restricted["rows"]), 2)

    def test_utc_offsets_zero_window_and_duplicates(self):
        rows = samples()
        rows[1]["timestamp"] = "2026-10-26T08:00:01+08:00"
        rows.insert(2, dict(rows[1]))
        detected = detect_anomalies(rows, 1)
        window = event_window(rows, detected, 0, 0)
        self.assertEqual([row["relative_seconds"] for row in window["rows"]], [0, 0, 1])
        self.assertEqual(len(window["points"]), 3)

    def test_single_point_and_sampling_gap_are_not_interpolated(self):
        rows = samples()
        detected = detect_anomalies(rows, 1)
        last = next(i for i, event in enumerate(detected["events"]) if event["start_timestamp"] == rows[-1]["timestamp"])
        window = event_window(rows, detected, last, 1)
        self.assertEqual(window["duration"], 0)
        self.assertEqual([row["relative_seconds"] for row in window["rows"]], [-1, 0])

    def test_inputs_are_unchanged_and_other_axis_not_highlighted(self):
        rows = samples()
        detected = detect_anomalies(rows, 1)
        before = deepcopy((rows, detected))
        window = event_window(rows, detected, 0, 60)
        self.assertTrue(all(point["field"] == "lateral_accel" for point in window["points"]))
        self.assertEqual((rows, detected), before)

    def test_invalid_window_and_selection_rejected(self):
        for index, seconds in ((True, 1), (-1, 1), (100, 1), (0, True), (0, -1), (0, 61), (0, float("nan"))):
            with self.subTest(index=index, seconds=seconds), self.assertRaises(ValueError):
                event_window(samples(), detect_anomalies(samples(), 1), index, seconds)

    def test_dense_window_and_markers_bounded_without_dropping_source_rows(self):
        rows = [dict(samples()[1]) for _ in range(3000)]
        window = event_window(rows, detect_anomalies(rows, 1), 0, 0)
        self.assertEqual(len(window["rows"]), 3000)
        self.assertEqual(len(window["points"]), 3000)
        self.assertEqual(len(window["visible"]), 2000)
        self.assertEqual(len(window["visible_points"]), 2000)

    def test_chart_has_existing_points_threshold_and_event_boundaries(self):
        detected = detect_anomalies(samples(), 1)
        chart = event_chart(event_window(samples(), detected, 0, 5), 1)
        self.assertEqual(len(chart.data), 3)
        self.assertTrue(all(trace.mode == "markers" for trace in chart.data))
        self.assertEqual(list(chart.data[-1].y), [2, -3])
        self.assertEqual([shape.y0 for shape in chart.layout.shapes if shape.type == "line" and shape.y0 == shape.y1], [-1, 1])

    def app(self):
        app = AppTest.from_function(review_app)
        app.session_state["samples"] = samples()
        app.session_state["anomalies"] = detect_anomalies(samples(), 1)
        app.session_state["selection"] = dict(vehicle=1, batch=2)
        return app.run()

    def test_page_selects_event_window_and_resets_selection_on_new_input(self):
        app = self.app()
        self.assertFalse(app.exception)
        app.selectbox(key="anomaly_review_event").select(3).run()
        self.assertIn("垂向", str(app.table[0].value))
        app.slider(key="anomaly_review_seconds").set_value(0).run()
        self.assertFalse(app.exception)
        app.session_state["selection"] = dict(vehicle=1, batch=3)
        app.run()
        self.assertEqual(app.selectbox(key="anomaly_review_event").value, 0)
        self.assertEqual(len(app.get("plotly_chart")), 1)

    def test_no_events_clears_previous_selection_without_chart(self):
        app = self.app()
        app.session_state["anomalies"] = dict(events=[], points=[], threshold=10)
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertNotIn("anomaly_review_event", app.session_state)

    def test_single_batch_metrics_ai_context_and_pdf_remain_stable_during_review(self):
        from rail_vehicle.pages import run_analysis
        with patch.object(run_analysis, "_render_ai_interpretation") as ai:
            app = AppTest.from_function(analysis_app).run()
            self.assertFalse(app.exception)
            context = deepcopy(ai.call_args.kwargs["payload"])
            metrics = [(metric.label, metric.value) for metric in app.metric]
            app.button(key="generate_analysis_report").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            pdf = app.session_state["analysis_report_pdf"]
            self.assertTrue(pdf.startswith(b"%PDF"))
            app.selectbox(key="anomaly_review_event").select(3).run()
            app.slider(key="anomaly_review_seconds").set_value(0).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["analysis_report_pdf"], pdf)
            self.assertEqual(ai.call_args.kwargs["payload"], context)
            self.assertEqual([(metric.label, metric.value) for metric in app.metric], metrics)
            self.assertEqual(len(app.get("plotly_chart")), 4)
