"""Tests for basic running-data dynamics statistics."""

import math
import unittest

from rail_vehicle.dynamics import analyze_run_data


def sample(timestamp, speed, lateral, vertical):
    return {
        "timestamp": timestamp,
        "speed_kmh": speed,
        "lateral_accel": lateral,
        "vertical_accel": vertical,
    }


class DynamicsAnalysisTests(unittest.TestCase):
    def setUp(self):
        # Deliberately unsorted to check that peak timestamps follow run time.
        self.samples = [
            sample("2026-10-08 09:30:02", 30, -2, 4),
            sample("2026-10-08 09:30:01", 10, 1, -1),
            sample("2026-10-08 09:30:03", 20, 2, -3),
        ]

    def test_speed_statistics(self):
        result = analyze_run_data(self.samples)
        self.assertEqual(result["speed"]["maximum"], 30)
        self.assertEqual(result["speed"]["minimum"], 10)
        self.assertEqual(result["speed"]["mean"], 20)

    def test_acceleration_mean_rms_and_extrema(self):
        result = analyze_run_data(self.samples)
        lateral = result["lateral_accel"]
        self.assertEqual(lateral["maximum"], 2)
        self.assertEqual(lateral["minimum"], -2)
        self.assertAlmostEqual(lateral["mean"], 1 / 3)
        self.assertAlmostEqual(lateral["rms"], math.sqrt(3))

        vertical = result["vertical_accel"]
        self.assertEqual(vertical["maximum"], 4)
        self.assertEqual(vertical["minimum"], -3)
        self.assertEqual(vertical["mean"], 0)
        self.assertAlmostEqual(vertical["rms"], math.sqrt(26 / 3))

    def test_absolute_peaks_include_signed_value_and_time(self):
        result = analyze_run_data(self.samples)
        lateral = result["lateral_accel"]
        self.assertEqual(lateral["peak_absolute"], 2)
        self.assertEqual(lateral["peak_signed"], -2)
        self.assertEqual(lateral["peak_timestamp"], "2026-10-08 09:30:02")

        vertical = result["vertical_accel"]
        self.assertEqual(vertical["peak_absolute"], 4)
        self.assertEqual(vertical["peak_signed"], 4)
        self.assertEqual(vertical["peak_timestamp"], "2026-10-08 09:30:02")

    def test_threshold_counts_absolute_exceedances_and_unique_rows(self):
        result = analyze_run_data(self.samples, acceleration_threshold=1.5)
        threshold = result["threshold"]
        self.assertEqual(threshold["lateral"]["count"], 2)
        self.assertAlmostEqual(threshold["lateral"]["percentage"], 200 / 3)
        self.assertEqual(threshold["vertical"]["count"], 2)
        self.assertEqual(threshold["either"]["count"], 2)
        self.assertAlmostEqual(threshold["either"]["percentage"], 200 / 3)

    def test_threshold_comparison_is_strict(self):
        result = analyze_run_data(self.samples, acceleration_threshold=2)
        self.assertEqual(result["threshold"]["lateral"]["count"], 0)
        self.assertEqual(result["threshold"]["vertical"]["count"], 2)

    def test_empty_samples_negative_threshold_and_nonfinite_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "没有运行数据"):
            analyze_run_data([])
        with self.assertRaisesRegex(ValueError, "阈值"):
            analyze_run_data(self.samples, acceleration_threshold=-1)
        invalid_samples = [sample("2026-10-08 09:30:00", float("nan"), 0, 0)]
        with self.assertRaisesRegex(ValueError, "有限数值"):
            analyze_run_data(invalid_samples)


if __name__ == "__main__":
    unittest.main()
