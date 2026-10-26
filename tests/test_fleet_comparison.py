"""Cross-vehicle numerical and interactive acceptance checks."""

from copy import deepcopy
import math
import sqlite3
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.fleet_comparison import compare_vehicles
from rail_vehicle.pages import fleet_comparison as page


def fleet_app():
    from rail_vehicle.pages.fleet_comparison import render
    render()


class FleetCalculationTests(unittest.TestCase):
    def setUp(self):
        self.entries = page.demo_entries()

    def test_reuses_statistics_without_mutating_input(self):
        original = deepcopy(self.entries)
        result = compare_vehicles(self.entries)
        self.assertEqual(self.entries, original)
        first = result["results"][0]
        self.assertEqual(first["sample_count"], 400)
        expected = math.sqrt(sum(r["lateral_accel"] ** 2 for r in self.entries[0]["samples"]) / 400)
        self.assertAlmostEqual(first["lateral_accel_rms"], expected)
        self.assertEqual(first["series"][0]["relative_time_seconds"], 0)

    def test_shared_closed_band_keeps_time_gaps(self):
        rows = [dict(timestamp=f"2026-01-01T00:00:0{i}Z", speed_kmh=s,
                     lateral_accel=0.1, vertical_accel=-0.2) for i, s in enumerate((70, 90, 80))]
        for entry in self.entries:
            entry["samples"] = deepcopy(rows)
        results = compare_vehicles(self.entries, 70, 80)["results"]
        for result in results:
            self.assertEqual(result["sample_count"], 2)
            self.assertEqual(result["mean_speed_kmh"], 75)
            self.assertEqual(result["duration_seconds"], 2)
            self.assertEqual(result["retained_fraction"], 2 / 3)

    def test_empty_vehicle_band_rejects_whole_comparison(self):
        with self.assertRaises(ValueError):
            compare_vehicles(self.entries, 400, 500)

    def test_duplicate_vehicles_and_bad_ranges_rejected(self):
        with self.assertRaises(ValueError):
            compare_vehicles([self.entries[0], self.entries[0]])
        for low, high in ((80, 70), (-1, 80), (0, 601), (0, float("nan"))):
            with self.subTest(low=low, high=high), self.assertRaises(ValueError):
                compare_vehicles(self.entries, low, high)

    def test_metadata_and_sampling_warnings_are_explicit(self):
        self.entries[1]["vehicle"]["mass_kg"] = 43000
        self.entries[1]["samples"] = self.entries[1]["samples"][::2]
        warnings = " ".join(compare_vehicles(self.entries)["warnings"])
        self.assertIn("登记质量", warnings)
        self.assertIn("采样间隔", warnings)
        self.assertIn("不进行车辆优劣", warnings)

    def test_duplicate_timestamps_reported_not_discarded(self):
        self.entries[0]["samples"].append(deepcopy(self.entries[0]["samples"][0]))
        result = compare_vehicles(self.entries)
        self.assertEqual(result["results"][0]["sample_count"], 401)
        self.assertTrue(any("重复时间戳" in w for w in result["warnings"]))


class FleetPageTests(unittest.TestCase):
    def demo(self):
        with patch.object(page, "list_vehicles", return_value=[]):
            app = AppTest.from_function(fleet_app).run()
            app.radio[0].set_value("内置虚构对比").run()
        return app

    def test_demo_calculates_on_click_and_changed_range_hides_results(self):
        app = self.demo()
        self.assertEqual(len(app.dataframe), 0)
        app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 2)
        self.assertEqual(len(app.get("plotly_chart")), 3)
        app.slider[0].set_value((70, 80)).run()
        self.assertEqual(len(app.dataframe), 0)
        app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 2)

    def test_empty_band_explains_failure_without_partial_results(self):
        app = self.demo()
        app.slider[0].set_value((400, 500)).run()
        app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 0)
        self.assertTrue(any("无法对比" in w.value for w in app.warning))

    def test_history_selection_loads_each_vehicle_batch(self):
        entries = page.demo_entries()
        by_id = {e["vehicle"]["id"]: e for e in entries}
        with patch.object(page, "list_vehicles", return_value=[e["vehicle"] for e in entries]), \
             patch.object(page, "list_vehicle_batches", return_value=[dict(id=7, sample_count=160)]), \
             patch.object(page, "list_batch_samples", side_effect=lambda vid, bid: by_id[vid]["samples"]) as load:
            app = AppTest.from_function(fleet_app).run()
            app.multiselect[0].set_value([1, 2]).run()
            app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe), 2)
        self.assertIn(((1, 7),), [(call.args,) for call in load.call_args_list])

    def test_database_error_has_safe_message(self):
        with patch.object(page, "list_vehicles", side_effect=sqlite3.OperationalError("PRIVATE_PATH")):
            app = AppTest.from_function(fleet_app).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("暂时无法读取", app.error[0].value)
        self.assertNotIn("PRIVATE_PATH", app.error[0].value)
