"""Tests for analysis report payloads and PDF generation."""

from datetime import datetime, timedelta, timezone
import unittest

from reportlab.lib import colors

from rail_vehicle.analysis_report import (
    MAX_CHART_POINTS,
    REPORT_TITLE,
    _chart_drawing,
    _comparison_chart_drawing,
    build_analysis_report_data,
    generate_analysis_report_pdf,
)
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.dynamics import analyze_run_data


def sample(second, speed, lateral, vertical):
    return {
        "timestamp": f"2026-10-08 09:30:{second:02d}",
        "speed_kmh": speed,
        "lateral_accel": lateral,
        "vertical_accel": vertical,
    }


class AnalysisReportTests(unittest.TestCase):
    def setUp(self):
        self.unfiltered_samples = [
            sample(0, 500, 50, 50),
            sample(1, 50, 0.5, 0),
            sample(2, 80, 2, -3),
            sample(3, 0, 30, 30),
        ]
        # This is the already time-filtered sample set passed by the page.
        self.samples = self.unfiltered_samples[1:3]
        self.analysis = analyze_run_data(self.samples, acceleration_threshold=1)
        self.anomalies = detect_anomalies(self.analysis["samples"], 1)
        self.vehicle = {
            "vehicle_code": "RV-TEST",
            "vehicle_type": "试验车型",
            "vehicle_length_m": 24.5,
            "mass_kg": 42000,
            "axle_count": 4,
            "bogie_count": 2,
            "max_speed_kmh": 160,
        }
        self.batch = {
            "id": 7,
            "file_name": "filtered-run.csv",
            "imported_at": "2026-10-08 10:00:00",
        }
        self.report = build_analysis_report_data(
            self.vehicle,
            self.batch,
            self.analysis,
            self.anomalies,
            datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc),
            datetime(2026, 10, 8, 9, 31, tzinfo=timezone.utc),
            generated_at=datetime(2026, 10, 8, 10, tzinfo=timezone.utc),
        )

    def test_payload_contains_vehicle_batch_selected_range_and_metrics(self):
        report = self.report
        self.assertEqual(report["title"], REPORT_TITLE)
        self.assertEqual(report["vehicle"]["vehicle_code"], "RV-TEST")
        self.assertEqual(report["vehicle"]["vehicle_type"], "试验车型")
        self.assertEqual(report["vehicle"]["mass_kg"], 42000)
        self.assertEqual(report["vehicle"]["axle_count"], 4)
        self.assertEqual(report["vehicle"]["bogie_count"], 2)
        self.assertEqual(report["vehicle"]["max_speed_kmh"], 160)
        self.assertEqual(report["batch"]["file_name"], "filtered-run.csv")
        self.assertEqual(report["filter_range"]["start"], "2026-10-08 09:30:00 UTC")
        self.assertEqual(report["filter_range"]["end"], "2026-10-08 09:31:00 UTC")
        self.assertEqual(report["sample_count"], 2)
        self.assertEqual(report["speed"]["mean"], 65)
        self.assertEqual(report["speed"]["maximum"], 80)
        self.assertEqual(report["lateral_accel"]["minimum"], 0.5)
        self.assertEqual(report["lateral_accel"]["peak_absolute"], 2)
        self.assertEqual(report["vertical_accel"]["minimum"], -3)
        self.assertEqual(report["vertical_accel"]["peak_absolute"], 3)
        self.assertEqual(report["data_range"]["start"], "2026-10-08 09:30:01")
        self.assertEqual(report["data_range"]["end"], "2026-10-08 09:30:02")

    def test_payload_contains_threshold_proportions_and_anomaly_events(self):
        report = self.report
        self.assertEqual(report["threshold"]["value"], 1)
        self.assertEqual(report["threshold"]["lateral"]["count"], 1)
        self.assertEqual(report["threshold"]["lateral"]["percentage"], 50)
        self.assertEqual(report["threshold"]["vertical"]["count"], 1)
        self.assertEqual(report["threshold"]["either"]["count"], 1)
        self.assertEqual(len(report["anomaly_events"]), 2)
        self.assertEqual(report["anomaly_events"][0]["sample_count"], 1)

    def test_report_requires_filtered_samples(self):
        empty_analysis = {
            **self.analysis,
            "samples": [],
        }
        with self.assertRaisesRegex(ValueError, "没有筛选后的运行数据"):
            build_analysis_report_data(
                self.vehicle,
                self.batch,
                empty_analysis,
                {"events": []},
                datetime(2026, 10, 8, 9, 30),
                datetime(2026, 10, 8, 9, 31),
            )

    def test_generates_a_pdf_document(self):
        output = generate_analysis_report_pdf(self.report)
        self.assertTrue(output.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", output[-32:])
        self.assertGreater(len(output), 10_000)

    def test_single_and_comparison_charts_cap_points_and_preserve_endpoints(self):
        source_count = MAX_CHART_POINTS * 10
        samples = [
            {
                "timestamp": (
                    datetime(2026, 10, 8, tzinfo=timezone.utc)
                    + timedelta(seconds=index)
                ).isoformat(),
                "speed_kmh": float(index),
                "lateral_accel": float(index),
                "vertical_accel": float(-index),
            }
            for index in range(source_count)
        ]
        single_drawing = _chart_drawing(
            samples, "speed_kmh", "速度", [colors.blue], "Helvetica"
        )
        single_plot = single_drawing.contents[0]
        self.assertEqual(len(single_plot.data[0]), MAX_CHART_POINTS)
        self.assertEqual(single_plot.data[0][0], (0.0, 0.0))
        self.assertEqual(single_plot.data[0][-1], (source_count - 1.0, source_count - 1.0))

        short_series = [
            {"relative_time_seconds": 0.0, "speed_kmh": 12.0},
            {"relative_time_seconds": 1.0, "speed_kmh": 13.0},
        ]
        comparison_drawing = _comparison_chart_drawing(
            [
                {
                    "batch_ref": "B1",
                    "series": [
                        {"relative_time_seconds": float(index), "speed_kmh": float(index)}
                        for index in range(source_count)
                    ],
                },
                {"batch_ref": "B2", "series": short_series},
            ],
            "speed_kmh",
            "速度",
            "Helvetica",
        )
        comparison_plot = comparison_drawing.contents[0]
        self.assertEqual(len(comparison_plot.data[0]), MAX_CHART_POINTS)
        self.assertEqual(len(comparison_plot.data[1]), len(short_series))
        self.assertEqual(comparison_plot.data[0][0], (0.0, 0.0))
        self.assertEqual(
            comparison_plot.data[0][-1],
            (source_count - 1.0, source_count - 1.0),
        )


if __name__ == "__main__":
    unittest.main()
