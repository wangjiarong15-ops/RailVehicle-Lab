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
from rail_vehicle.dashboard_chart import dashboard_chart
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
