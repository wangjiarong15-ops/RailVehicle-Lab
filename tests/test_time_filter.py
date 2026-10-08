"""Tests for inclusive running-data time-range filtering."""

from datetime import datetime, timezone
import unittest

from rail_vehicle.time_filter import filter_samples_by_time_range


class TimeRangeFilterTests(unittest.TestCase):
    def setUp(self):
        self.samples = [
            {"timestamp": "2026-10-08 09:30:02", "value": 2},
            {"timestamp": "2026-10-08 09:30:00", "value": 0},
            {"timestamp": "2026-10-08 09:30:01", "value": 1},
        ]

    def test_range_includes_both_boundaries_and_preserves_source_order(self):
        filtered = filter_samples_by_time_range(
            self.samples, "2026-10-08T09:30:00", "2026-10-08T09:30:01"
        )
        self.assertEqual([sample["value"] for sample in filtered], [0, 1])

    def test_empty_range_returns_empty_list(self):
        filtered = filter_samples_by_time_range(
            self.samples, "2026-10-08T09:31:00", "2026-10-08T09:32:00"
        )
        self.assertEqual(filtered, [])

    def test_offset_timestamps_are_compared_as_same_utc_instant(self):
        samples = [{"timestamp": "2026-10-08T11:30:00+02:00", "value": 1}]
        filtered = filter_samples_by_time_range(
            samples,
            datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc),
            datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(filtered, samples)

    def test_reversed_range_and_invalid_sample_timestamp_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "开始时间"):
            filter_samples_by_time_range(
                self.samples, "2026-10-08T09:31:00", "2026-10-08T09:30:00"
            )
        with self.assertRaisesRegex(ValueError, "时间戳无效"):
            filter_samples_by_time_range(
                [{"timestamp": "bad"}], "2026-10-08T09:30:00", "2026-10-08T09:31:00"
            )


if __name__ == "__main__":
    unittest.main()
