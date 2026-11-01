"""Tests for descriptive running-data sampling-time summaries."""

import unittest

from rail_vehicle.data_quality import analyze_sampling_times, review_sampling_timestamps
from datetime import datetime, timedelta, timezone


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
            [
                sample("2026-10-08T09:30:00Z"),
                sample("2026-10-08T09:30:00Z"),
                sample("2026-10-08T09:30:00Z"),
            ]
        )

        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["unique_timestamp_count"], 1)
        self.assertEqual(result["duplicate_timestamp_rows"], 2)
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


class SamplingQualityReviewTests(unittest.TestCase):
    def review(self, offsets, **kwargs):
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        return review_sampling_timestamps((start + timedelta(seconds=x) for x in offsets), **kwargs)

    def test_gap_details_and_input_reversals_preserve_source_order(self):
        result = self.review([0, 1, 2, 12, 11, 12])
        self.assertEqual(result["duplicate_timestamp_rows"], 1)
        self.assertEqual(result["out_of_order_pairs"], 1)
        self.assertEqual(result["baseline_seconds"], 1)
        self.assertEqual(result["long_gap_count"], 1)
        self.assertEqual(result["long_gaps"][0]["interval_seconds"], 9)
        self.assertEqual(result["long_gaps"][0]["start_timestamp"], "2026-11-01T00:00:02+00:00")
        self.assertEqual(result["status"], "needs_review")

    def test_known_interval_catches_systematic_missing_samples(self):
        self.assertEqual(self.review([0, 10, 20])["status"], "no_flags")
        specified = self.review([0, 10, 20], expected_interval_seconds=1)
        self.assertEqual(specified["long_gap_count"], 2)
        self.assertEqual(specified["baseline_source"], "specified")

    def test_strict_boundary_and_fractional_interval(self):
        result = self.review([0, .01, .04, .08], expected_interval_seconds=.01)
        self.assertEqual(result["long_gap_count"], 1)
        self.assertEqual(result["long_gaps"][0]["interval_seconds"], .04)

    def test_absolute_utc_duplicate_and_reversal_semantics(self):
        result = review_sampling_timestamps(["2026-11-01T08:00:01+08:00", "2026-11-01T00:00:00Z",
                                             "2026-11-01T00:00:00"])
        self.assertEqual(result["duplicate_timestamp_rows"], 1)
        self.assertEqual(result["out_of_order_pairs"], 1)

    def test_short_and_duplicate_only_inputs(self):
        self.assertEqual(self.review([0])["status"], "insufficient")
        result = self.review([0, 0])
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["gap_cutoff_seconds"])
        self.assertEqual(result["long_gap_count"], 0)

    def test_detail_limit_does_not_truncate_counts(self):
        result = self.review(range(0, 2100, 10), expected_interval_seconds=1)
        self.assertEqual(result["long_gap_count"], 209)
        self.assertEqual(len(result["long_gaps"]), 100)
        self.assertTrue(result["details_truncated"])
        result = self.review([0, 10], expected_interval_seconds=1, detail_limit=0)
        self.assertEqual(result["long_gaps"], [])
        self.assertEqual(result["long_gap_count"], 1)

    def test_invalid_rules_and_timestamps_are_rejected(self):
        for value in [0, -1, float("nan"), float("inf"), True, "1"]:
            with self.subTest(expected=value), self.assertRaises(ValueError):
                self.review([0, 1], expected_interval_seconds=value)
        for value in [0, 1, float("nan"), float("inf"), True, "3"]:
            with self.subTest(multiplier=value), self.assertRaises(ValueError):
                self.review([0, 1], gap_multiplier=value)
        for value in [-1, 1.5, True]:
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.review([0, 1], detail_limit=value)
        for values in [[], ["bad"], None]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                review_sampling_timestamps(values)


if __name__ == "__main__":
    unittest.main()
