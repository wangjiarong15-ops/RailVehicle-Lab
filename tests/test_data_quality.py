"""Tests for descriptive running-data sampling-time summaries."""

import unittest

from rail_vehicle.data_quality import analyze_sampling_times


def sample(timestamp):
    return {"timestamp": timestamp}


class SamplingTimeAnalysisTests(unittest.TestCase):
    def test_intervals_are_sorted_and_summarized(self):
        samples = [
            sample("2026-10-08T09:30:03Z"),
            sample("2026-10-08T09:30:00Z"),
            sample("2026-10-08T09:30:01Z"),
        ]
        result = analyze_sampling_times(iter(samples))

        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["unique_timestamp_count"], 3)
        self.assertEqual(result["duplicate_timestamp_rows"], 0)
        self.assertEqual(result["interval_min_seconds"], 1)
        self.assertEqual(result["interval_median_seconds"], 1.5)
        self.assertEqual(result["interval_max_seconds"], 2)

    def test_duplicate_instants_across_utc_offsets_are_counted(self):
        result = analyze_sampling_times(
            [
                sample("2026-10-08T11:30:00+02:00"),
                sample("2026-10-08T09:30:00Z"),
                sample("2026-10-08T09:30:02Z"),
            ]
        )

        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["unique_timestamp_count"], 2)
        self.assertEqual(result["duplicate_timestamp_rows"], 1)
        self.assertEqual(result["interval_min_seconds"], 2)
        self.assertEqual(result["interval_median_seconds"], 2)
        self.assertEqual(result["interval_max_seconds"], 2)

    def test_single_unique_timestamp_has_no_interval_statistics(self):
        result = analyze_sampling_times(
            [sample("2026-10-08T09:30:00Z"), sample("2026-10-08T09:30:00Z")]
        )

        self.assertEqual(result["duplicate_timestamp_rows"], 1)
        self.assertIsNone(result["interval_min_seconds"])
        self.assertIsNone(result["interval_median_seconds"])
        self.assertIsNone(result["interval_max_seconds"])

    def test_empty_or_invalid_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "没有运行数据"):
            analyze_sampling_times([])
        with self.assertRaisesRegex(ValueError, "无效的时间戳"):
            analyze_sampling_times([sample("not-a-timestamp")])
        with self.assertRaisesRegex(ValueError, "无效的时间戳"):
            analyze_sampling_times([{}])


if __name__ == "__main__":
    unittest.main()
