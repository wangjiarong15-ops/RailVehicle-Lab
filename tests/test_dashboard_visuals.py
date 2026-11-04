"""Presentation data must retain computed facts and native theme support."""
from copy import deepcopy
from pathlib import Path
import streamlit as st
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from urllib.parse import parse_qs, urlsplit

from rail_vehicle.demo_data import analysis_demo_data
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.dashboard_chart import dashboard_chart, dashboard_events, display_timestamp
from rail_vehicle.ui import theme_url


class DashboardVisualTests(unittest.TestCase):
    def setUp(self):
        rows = analysis_demo_data()[2][-1]
        self.analysis = analyze_run_data(rows, 1.)
        self.anomalies = detect_anomalies(rows, 1.)

    def test_three_channels_and_anomaly_markers_copy_existing_facts(self):
        before = deepcopy((self.analysis, self.anomalies))
        figure = dashboard_chart(self.analysis, self.anomalies)
        self.assertEqual(len(figure.data), 5)
        for index, field in ((0,"speed_kmh"),(1,"lateral_accel"),(3,"vertical_accel")):
            self.assertEqual(list(figure.data[index].y), [row[field] for row in self.analysis["samples"]])
            self.assertEqual(figure.data[index].mode, "markers")
        # Anomaly traces must not consume a color slot and change channel identity.
        self.assertEqual([figure.data[i].marker.color for i in (0, 1, 3)],
                         ["#4299E8", "#24B9B0", "#A38CF0"])
        self.assertEqual(len(figure.data[2].y), 3)
        self.assertEqual(len(figure.data[4].y), 2)
        self.assertEqual((self.analysis, self.anomalies), before)

    def test_shading_uses_exact_existing_event_boundaries(self):
        figure = dashboard_chart(self.analysis, self.anomalies)
        rectangles = [shape for shape in figure.layout.shapes if shape.type == "rect"]
        self.assertEqual(len(rectangles), 2)
        for shape,event in zip(rectangles,self.anomalies["events"]):
            self.assertEqual(shape.x0.isoformat().replace("+00:00","Z"), event["start_timestamp"])
            self.assertEqual(shape.x1.isoformat().replace("+00:00","Z"), event["end_timestamp"])

    def test_single_sample_events_and_dense_input_have_bounded_rendering(self):
        row = dict(self.analysis["samples"][0], lateral_accel=2.)
        for count in (1, 3000):
            with self.subTest(count=count):
                analysis = analyze_run_data([dict(row) for _ in range(count)],1.)
                anomalies = detect_anomalies(analysis["samples"],1.)
                figure = dashboard_chart(analysis,anomalies)
                self.assertTrue(all(len(trace.x)<=1200 for trace in figure.data))
                self.assertTrue(any(shape.x0==shape.x1 for shape in figure.layout.shapes))

    def test_numbered_event_pages_and_threshold_annotations_match_data(self):
        anomalies = deepcopy(self.anomalies)
        anomalies["events"] = [deepcopy(anomalies["events"][i % 2]) for i in range(35)]
        anomalies["threshold"] = 1.25
        first, second = dashboard_events(anomalies, 0), dashboard_events(anomalies, 1)
        self.assertEqual([n for n, _ in first], list(range(1, 31)))
        self.assertEqual([n for n, _ in second], list(range(31, 36)))
        chart = dashboard_chart(self.analysis, anomalies, 1)
        labels = [annotation.text for annotation in chart.layout.annotations]
        self.assertEqual([item for item in labels if item.startswith("#")], [f"#{i}" for i in range(31, 36)])
        self.assertEqual(labels.count("+1.25 m/s²"), 2)
        self.assertEqual(labels.count("-1.25 m/s²"), 2)
        self.assertEqual(chart.layout.hovermode, "x unified")
        if hasattr(chart.layout, "hoversubplots"):
            self.assertEqual(chart.layout.hoversubplots, "axis")
        self.assertTrue(all(trace.xaxis == "x" for trace in chart.data))
        self.assertTrue(all(shape.xref not in ("x2", "x3") for shape in chart.layout.shapes))
        self.assertEqual(chart.layout.xaxis.anchor, "y3")
        self.assertNotIn("xaxis2", chart.layout.to_plotly_json())
        self.assertNotIn("xaxis3", chart.layout.to_plotly_json())
        self.assertTrue(all(shape.xref in ("x", "x domain") for shape in chart.layout.shapes))

    def test_timezone_changes_clocks_not_values_events_or_instants(self):
        before = deepcopy((self.analysis, self.anomalies))
        utc_chart = dashboard_chart(self.analysis, self.anomalies)
        local_chart = dashboard_chart(self.analysis, self.anomalies, timezone_name="Asia/Shanghai")
        for original, localized in zip(utc_chart.data, local_chart.data):
            self.assertEqual(list(original.y), list(localized.y))
            self.assertEqual(list(original.x), list(localized.x))  # same absolute instants
            if localized.customdata is not None:
                self.assertTrue(all(stamp.endswith("+08:00") for stamp in localized.customdata))
        self.assertIn("Asia/Shanghai", local_chart.layout.xaxis.title.text)
        self.assertEqual([a.text for a in utc_chart.layout.annotations], [a.text for a in local_chart.layout.annotations])
        self.assertEqual((self.analysis, self.anomalies), before)

    def test_display_timezone_handles_offsets_naive_utc_and_day_rollover(self):
        local = display_timestamp("2026-11-03T20:30:00Z", "Asia/Shanghai")
        self.assertEqual(local.isoformat(), "2026-11-04T04:30:00+08:00")
        self.assertEqual(display_timestamp("2026-11-03T20:30:00", "Asia/Shanghai"), local)
        self.assertEqual(display_timestamp("2026-11-04T04:30:00+08:00"), display_timestamp("2026-11-03T20:30:00Z"))
        with self.assertRaises(ValueError):
            display_timestamp("2026-11-03T00:00:00Z", "unknown")

    def test_native_theme_urls_preserve_page_and_reject_unknown_modes(self):
        for mode in ("light","dark"):
            query = parse_qs(urlsplit(theme_url(mode,"车辆参数")).query)
            self.assertEqual(query["embed_options"], [mode+"_theme"])
            self.assertEqual(query["rail_page"], ["车辆参数"])
        with self.assertRaises(ValueError):
            theme_url("unknown","平台首页")

    def test_both_native_palettes_cover_sidebar_tables_and_charts(self):
        self.assertEqual(st.get_option("theme.base"), "dark")
        for mode in ("light","dark"):
            for key in ("backgroundColor","secondaryBackgroundColor","textColor","dataframeHeaderBackgroundColor","chartCategoricalColors","sidebar.backgroundColor"):
                self.assertIsNotNone(st.get_option(f"theme.{mode}.{key}"))

    def test_theme_bootstrap_runs_after_page_rendering_not_during_lazy_imports(self):
        events = []
        with patch("rail_vehicle.db.init_db"), \
             patch("rail_vehicle.pages.home.render", side_effect=lambda: events.append("page")), \
             patch("rail_vehicle.ui.initialize_theme", side_effect=lambda: events.append("theme")):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"), default_timeout=15)
            app.session_state["navigation_page"] = "平台首页"
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(events, ["page", "theme"])
