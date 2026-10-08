"""Tests for running-data batch comparison calculations."""

import math
import unittest

from rail_vehicle.batch_comparison import compare_run_batches


def sample(timestamp, speed, lateral, vertical):
    return {
        "timestamp": timestamp,
        "speed_kmh": speed,
        "lateral_accel": lateral,
        "vertical_accel": vertical,
    }


class BatchComparisonTests(unittest.TestCase):
    def setUp(self):
        self.batches = [
            {
                "batch_id": 1,
                "label": "run-a.csv",
                "samples": [
                    sample("2026-10-08 10:00:12", 30, -4, 0),
                    sample("2026-10-08 10:00:10", 10, 3, 4),
                ],
            },
            {
                "batch_id": 2,
                "label": "run-b.csv",
                "samples": [
                    sample("2026-10-09 11:00:02", 40, 0, -6),
                    sample("2026-10-09 11:00:00", 20, -2, 2),
                ],
            },
        ]

    def test_computes_required_metrics_for_each_batch(self):
        first, second = compare_run_batches(self.batches)
        self.assertEqual(first["sample_count"], 2)
        self.assertEqual(first["mean_speed_kmh"], 20)
        self.assertEqual(first["maximum_speed_kmh"], 30)
        self.assertAlmostEqual(first["lateral_accel_rms"], math.sqrt(12.5))
        self.assertAlmostEqual(first["vertical_accel_rms"], math.sqrt(8))
        self.assertEqual(first["lateral_accel_peak_absolute"], 4)
        self.assertEqual(first["vertical_accel_peak_absolute"], 4)
        self.assertEqual(second["mean_speed_kmh"], 30)
        self.assertEqual(second["maximum_speed_kmh"], 40)
        self.assertEqual(second["lateral_accel_peak_absolute"], 2)
        self.assertEqual(second["vertical_accel_peak_absolute"], 6)

    def test_relative_time_starts_at_zero_and_samples_are_chronological(self):
        comparisons = compare_run_batches(self.batches)
        for comparison in comparisons:
            self.assertEqual(
                [point["relative_time_seconds"] for point in comparison["series"]],
                [0, 2],
            )
        self.assertEqual(comparisons[0]["series"][0]["speed_kmh"], 10)

    def test_batches_with_different_absolute_times_share_relative_axis(self):
        comparisons = compare_run_batches(self.batches)
        self.assertEqual(
            comparisons[0]["series"][0]["relative_time_seconds"],
            comparisons[1]["series"][0]["relative_time_seconds"],
        )

    def test_fractional_seconds_are_preserved(self):
        batches = [
            {
                **self.batches[0],
                "samples": [
                    sample("2026-10-08T10:00:00.250", 1, 0, 0),
                    sample("2026-10-08T10:00:01.750", 2, 0, 0),
                ],
            },
            self.batches[1],
        ]
        result = compare_run_batches(batches)
        self.assertEqual(result[0]["series"][0]["relative_time_seconds"], 0)
        self.assertEqual(result[0]["series"][1]["relative_time_seconds"], 1.5)

    def test_requires_at_least_two_batches(self):
        with self.assertRaisesRegex(ValueError, "至少需要选择两个"):
            compare_run_batches(self.batches[:1])

    def test_empty_or_invalid_batch_data_is_reported(self):
        invalid_batches = [
            {"batch_id": 1, "label": "empty.csv", "samples": []},
            self.batches[1],
        ]
        with self.assertRaisesRegex(ValueError, "没有运行数据"):
            compare_run_batches(invalid_batches)
        invalid_batches[0]["samples"] = [sample("bad", 1, 0, 0)]
        with self.assertRaisesRegex(ValueError, "时间戳"):
            compare_run_batches(invalid_batches)


if __name__ == "__main__":
    unittest.main()
