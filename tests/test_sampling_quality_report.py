"""Quality rules, filtered facts and bounded details survive PDF export."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from rail_vehicle.analysis_report import build_analysis_report_data, generate_analysis_report_pdf
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.dynamics import analyze_run_data
from tests.pdf_checks import assert_pdf_integrity


def fictional_quality_report(offsets, **rules):
    start = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)
    rows = [dict(timestamp=(start + timedelta(seconds=second)).isoformat(),
                 speed_kmh=80 + index % 3, lateral_accel=.1, vertical_accel=-.2)
            for index, second in enumerate(offsets)]
    analysis = analyze_run_data(rows, 1)
    return build_analysis_report_data(
        vehicle=dict(vehicle_code="FICTIONAL-QA", vehicle_type="虚构采样检查演示（非实测）",
                     vehicle_length_m=24, mass_kg=42000, axle_count=4, bogie_count=2, max_speed_kmh=160),
        batch=dict(id=-1, file_name="fictional_quality.csv", imported_at=start.isoformat()),
        analysis=analysis, anomalies=detect_anomalies(analysis["samples"], 1),
        selected_start=start, selected_end=start + timedelta(seconds=max(offsets)),
        generated_at=start, **rules,
    )


def story_text(report):
    with patch("rail_vehicle.analysis_report.SimpleDocTemplate.build", autospec=True) as build:
        build.side_effect = lambda document, story, **kwargs: document.filename.write(b"%PDF-test")
        generate_analysis_report_pdf(report)
    texts = []
    for item in build.call_args.args[1]:
        if hasattr(item, "getPlainText"):
            texts.append(item.getPlainText())
        if hasattr(item, "_cellvalues"):
            texts.extend(cell.getPlainText() for row in item._cellvalues for cell in row)
    return "\n".join(texts)


class SamplingQualityReportTests(unittest.TestCase):
    def test_default_rules_and_duplicate_gap_facts_use_report_samples(self):
        report = fictional_quality_report([0, 1, 1, 2, 20])
        quality = report["sampling_quality"]
        self.assertEqual(quality["sample_count"], report["sample_count"])
        self.assertEqual(quality["duplicate_timestamp_rows"], 1)
        self.assertEqual(quality["long_gap_count"], 1)
        self.assertEqual(quality["long_gaps"][0]["interval_seconds"], 18)
        self.assertEqual(quality["baseline_source"], "median")
        self.assertEqual(quality["gap_cutoff_seconds"], 3)

    def test_manual_rules_change_quality_but_not_existing_analysis(self):
        automatic = fictional_quality_report([0, 10, 20])
        manual = fictional_quality_report([0, 10, 20], sampling_expected_interval_seconds=1,
                                          sampling_gap_multiplier=5)
        self.assertEqual(automatic["sampling_quality"]["long_gap_count"], 0)
        self.assertEqual(manual["sampling_quality"]["long_gap_count"], 2)
        self.assertEqual(manual["sampling_quality"]["baseline_source"], "specified")
        for key in ("samples", "speed", "lateral_accel", "vertical_accel", "threshold", "anomaly_events"):
            self.assertEqual(automatic[key], manual[key])

    def test_pdf_includes_rules_counts_and_utc_boundaries(self):
        report = fictional_quality_report([0, .01, .01, .5],
                                          sampling_expected_interval_seconds=.01)
        text = story_text(report)
        for expected in ("数据质量检查", "手动指定", "0.01", "0.03", "长采样间隔总数",
                         "重复时刻额外记录", "2026-11-01T09:30:00.010000+00:00",
                         "2026-11-01T09:30:00.500000+00:00", "不证明期间连续观测",
                         "不能代表原 CSV 行序"):
            self.assertIn(expected, text)

    def test_pdf_bounds_details_without_hiding_full_count(self):
        report = fictional_quality_report(list(range(0, 2100, 10)), sampling_expected_interval_seconds=1)
        text = story_text(report)
        self.assertIn("展示 20 / 209 处长间隔", text)
        self.assertIn("总数覆盖全部筛选样本", text)
        self.assertIn(report["sampling_quality"]["long_gaps"][19]["end_timestamp"], text)
        self.assertNotIn(report["sampling_quality"]["long_gaps"][20]["end_timestamp"], text)

    def test_normal_and_single_instant_messages_avoid_quality_certification(self):
        self.assertIn("不代表数据完整或质量合格", story_text(fictional_quality_report([0, 1, 2])))
        text = story_text(fictional_quality_report([0]))
        self.assertIn("不足两个独立时刻", text)
        self.assertIn("无法估计", text)
        self.assertNotIn("长采样间隔明细", text)

    def test_invalid_rules_are_rejected_before_pdf_generation(self):
        with self.assertRaises(ValueError):
            fictional_quality_report([0, 1], sampling_expected_interval_seconds=-1)
        with self.assertRaises(ValueError):
            fictional_quality_report([0, 1], sampling_gap_multiplier=float("nan"))

    def test_actual_pdf_renders_normal_duplicate_gap_and_dense_gap_inputs(self):
        for offsets in ([0, 1, 2], [0, 0, 1], [0, 1, 20], list(range(0, 2100, 10))):
            with self.subTest(offsets=offsets[:4]):
                report = fictional_quality_report(offsets, sampling_expected_interval_seconds=1)
                assert_pdf_integrity(self, generate_analysis_report_pdf(report))
