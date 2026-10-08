"""Tests for explainable threshold-based acceleration anomaly detection."""

import unittest

from rail_vehicle.anomaly import detect_anomalies


def sample(second, lateral, vertical):
    return {
        "timestamp": f"2026-10-08 09:30:{second:02d}",
        "lateral_accel": lateral,
        "vertical_accel": vertical,
    }


class AnomalyDetectionTests(unittest.TestCase):
    def setUp(self):
        self.samples = [
            sample(5, 2.2, 0),
            sample(3, 3, -1.5),
            sample(1, 1.5, 0),
            sample(4, 0, -3),
            sample(2, -2, 0.4),
        ]

    def test_classifies_axis_specific_points_with_strict_threshold(self):
        result = detect_anomalies(self.samples, acceleration_threshold=1.5)
        lateral_points = [
            point for point in result["points"] if point["field"] == "lateral_accel"
        ]
        vertical_points = [
            point for point in result["points"] if point["field"] == "vertical_accel"
        ]
        self.assertEqual([point["timestamp"][-2:] for point in lateral_points], ["02", "03", "05"])
        self.assertEqual([point["timestamp"][-2:] for point in vertical_points], ["04"])
        self.assertTrue(all(point["type"] == "横向加速度统计异常" for point in lateral_points))
        self.assertEqual(vertical_points[0]["type"], "垂向加速度统计异常")

    def test_consecutive_points_merge_and_normal_points_split_segments(self):
        result = detect_anomalies(self.samples, acceleration_threshold=1.5)
        lateral_events = [
            event for event in result["events"] if event["field"] == "lateral_accel"
        ]
        self.assertEqual(len(lateral_events), 2)
        merged = lateral_events[0]
        self.assertEqual(merged["start_timestamp"][-2:], "02")
        self.assertEqual(merged["end_timestamp"][-2:], "03")
        self.assertEqual(merged["duration_seconds"], 1)
        self.assertEqual(merged["sample_count"], 2)
        self.assertEqual(merged["maximum_absolute_value"], 3)
        self.assertEqual(merged["maximum_signed_value"], 3)
        self.assertEqual(merged["maximum_timestamp"][-2:], "03")

        single = lateral_events[1]
        self.assertEqual(single["start_timestamp"], single["end_timestamp"])
        self.assertEqual(single["duration_seconds"], 0)
        self.assertEqual(single["sample_count"], 1)

    def test_different_acceleration_types_are_reported_as_separate_events(self):
        result = detect_anomalies(self.samples, acceleration_threshold=1.5)
        self.assertEqual(
            {event["type"] for event in result["events"]},
            {"横向加速度统计异常", "垂向加速度统计异常"},
        )
        self.assertEqual(len(result["events"]), 3)

    def test_no_exceedance_returns_empty_point_and_event_lists(self):
        result = detect_anomalies([sample(1, 1, -1)], acceleration_threshold=1)
        self.assertEqual(result["points"], [])
        self.assertEqual(result["events"], [])

    def test_invalid_threshold_timestamp_or_acceleration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "阈值"):
            detect_anomalies(self.samples, float("nan"))
        with self.assertRaisesRegex(ValueError, "时间戳"):
            detect_anomalies([{"timestamp": "bad", "lateral_accel": 2, "vertical_accel": 0}], 1)
        with self.assertRaisesRegex(ValueError, "有限数值"):
            detect_anomalies([sample(1, float("inf"), 0)], 1)


if __name__ == "__main__":
    unittest.main()
