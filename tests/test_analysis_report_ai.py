"""PDF AI attachment tests; provider calls are represented by local validated data."""

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import rail_vehicle.analysis_report as report_module
from rail_vehicle.ai.context import build_batch_comparison_payload, build_single_batch_payload
from rail_vehicle.ai.provider import AIAnalysisService, FakeProvider, input_fingerprint
from rail_vehicle.ai.schemas import validate_output
from rail_vehicle.ai.ui import selection_fingerprint
from rail_vehicle.analysis_report import (
    build_analysis_report_data,
    build_batch_comparison_report_data,
    generate_analysis_report_pdf,
    generate_batch_comparison_report_pdf,
)
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.dynamics import analyze_run_data


def sample(timestamp, speed, lateral, vertical):
    return {"timestamp": timestamp, "speed_kmh": speed, "lateral_accel": lateral, "vertical_accel": vertical}


def valid_output(context):
    metric = "metric.speed.mean" if context["analysis_type"] == "single_batch" else "batch.B1.speed.mean"
    event_id = "event.lateral.001" if context["analysis_type"] == "single_batch" else metric
    return {
        "summary": {"text": "当前分析显示已计算的速度统计。", "evidence_ids": [metric]},
        "observations": [{"text": "存在已计算的运行指标。", "evidence_ids": [metric]}],
        "possible_causes": [{"text": "可能与运行工况变化有关。", "evidence_ids": [event_id], "qualification": "hypothesis"}],
        "further_checks": [{"action": "复核对应时段的记录。", "reason": "确认观察是否持续出现。", "evidence_ids": [metric]}],
        "limitations": [{"text": "解读范围受输入统计指标限制。", "evidence_ids": [metric]}],
    }


def story_text(flowable):
    chunks = []
    if hasattr(flowable, "getPlainText"):
        chunks.append(flowable.getPlainText())
    content = getattr(flowable, "_content", None)
    if content:
        chunks.extend(story_text(item) for item in content)
    cells = getattr(flowable, "_cellvalues", None)
    if cells:
        for row in cells:
            if isinstance(row, (list, tuple)):
                chunks.extend(story_text(item) for item in row if item is not None)
            elif row is not None:
                chunks.append(story_text(row))
    return " ".join(chunks)


class AnalysisReportAITests(unittest.TestCase):
    def setUp(self):
        self.samples = [
            sample("2026-10-08T09:30:01+00:00", 50, 0.5, 0),
            sample("2026-10-08T09:30:02+00:00", 80, 2, -3),
        ]
        self.analysis = analyze_run_data(self.samples, 1.0)
        self.anomalies = detect_anomalies(self.analysis["samples"], 1.0)
        self.vehicle = {
            "vehicle_code": "RV-PDF-TEST", "vehicle_type": "PDF测试车型",
            "vehicle_length_m": 24.5, "mass_kg": 42000, "axle_count": 4,
            "bogie_count": 2, "max_speed_kmh": 160,
        }
        self.batch = {"id": 7, "file_name": "private-run-file.csv", "imported_at": "2026-10-08 10:00:00"}
        self.single_context = build_single_batch_payload(
            analysis=self.analysis,
            anomalies=self.anomalies,
            vehicle=self.vehicle,
            batch_ref="B1",
            selected_start="2026-10-08T09:30:00+00:00",
            selected_end="2026-10-08T09:31:00+00:00",
        )
        self.single_selection = {"vehicle_id": 5, "batch_id": 7, "threshold": 1.0}
        self.single_result = self._success_result(self.single_context, self.single_selection, key="SHOULD_NEVER-APPEAR")

    @staticmethod
    def _success_result(context, selection, key=None):
        result = AIAnalysisService(FakeProvider(valid_output(context))).analyze(context)
        result["selection_fingerprint"] = selection_fingerprint(selection)
        result["ai_metadata"] = {
            "provider": "openai", "model": "configured-test-model",
            "generated_at": "2026-10-08T10:05:00+00:00", "status": "success",
        }
        if key:
            result["ai_metadata"]["api_key"] = key
        return result

    def _single_report(self, ai_result=None, context=None, selection_fp=None):
        return build_analysis_report_data(
            self.vehicle,
            self.batch,
            self.analysis,
            self.anomalies,
            datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc),
            datetime(2026, 10, 8, 9, 31, tzinfo=timezone.utc),
            generated_at=datetime(2026, 10, 8, 10, tzinfo=timezone.utc),
            ai_context=context or self.single_context,
            ai_result=ai_result,
            ai_selection_fingerprint=selection_fp or selection_fingerprint(self.single_selection),
        )

    def _generate_and_read_story(self, generator, report):
        captured = []
        real_build = report_module.SimpleDocTemplate.build

        def capture_build(document, story, *args, **kwargs):
            captured.extend(story)
            return real_build(document, story, *args, **kwargs)

        with patch.object(report_module.SimpleDocTemplate, "build", new=capture_build):
            pdf = generator(report)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        return pdf, " ".join(story_text(item) for item in captured)

    def test_single_batch_valid_ai_is_rendered_in_pdf_with_evidence_and_metadata(self):
        report = self._single_report(self.single_result, self.single_context)
        captured_rows = []
        real_table = report_module._table

        def capture_table(rows, *args, **kwargs):
            captured_rows.extend(rows)
            return real_table(rows, *args, **kwargs)

        with patch.object(report_module, "_table", new=capture_table):
            pdf, text = self._generate_and_read_story(generate_analysis_report_pdf, report)
        self.assertIn("AI 辅助解读", text)
        self.assertIn("当前分析显示已计算的速度统计。", text)
        self.assertIn("metric.speed.mean", text)
        self.assertIn("event.lateral.001", text)
        self.assertIn("AI Provider", repr(captured_rows))
        self.assertIn("openai", repr(captured_rows))
        self.assertIn("configured-test-model", repr(captured_rows))
        self.assertIn("2026-10-08T10:05:00+00:00", repr(captured_rows))
        self.assertIn("success", repr(captured_rows))
        self.assertIn("待核实的假设", text)
        self.assertIn("复核对应时段的记录", text)
        self.assertIn("确认观察是否持续出现", text)
        self.assertNotIn(b"SHOULD_NEVER-APPEAR", pdf)
        self.assertNotIn("SHOULD_NEVER-APPEAR", text)

    def test_no_ai_result_still_generates_complete_pdf_with_fallback_message(self):
        report = self._single_report()
        pdf, text = self._generate_and_read_story(generate_analysis_report_pdf, report)
        self.assertIn("AI 辅助解读未生成", text)
        self.assertGreater(len(pdf), 10000)

    def test_unavailable_and_validation_failed_results_do_not_break_pdf(self):
        for failed_result in (
            {"status": "unavailable", "code": "timeout"},
            {"status": "validation_failed", "code": "invalid_model_output"},
        ):
            with self.subTest(status=failed_result["status"]):
                report = self._single_report(failed_result, self.single_context)
                self.assertIsNone(report["ai_section"])
                pdf = generate_analysis_report_pdf(report)
                self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_context_fingerprint_mismatch_omits_ai_but_preserves_pdf(self):
        changed_context = dict(self.single_context)
        changed_context["threshold"] = dict(self.single_context["threshold"], value=9.0)
        report = self._single_report(self.single_result, changed_context)
        self.assertIsNone(report["ai_section"])
        pdf, text = self._generate_and_read_story(generate_analysis_report_pdf, report)
        self.assertIn("AI 辅助解读未生成", text)
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_selection_fingerprint_mismatch_omits_ai(self):
        report = self._single_report(
            self.single_result,
            self.single_context,
            selection_fingerprint({"vehicle_id": 99, "batch_id": 7, "threshold": 1.0}),
        )
        self.assertIsNone(report["ai_section"])

    def test_illegal_evidence_id_omits_ai_from_pdf(self):
        bad_output = valid_output(self.single_context)
        bad_output["summary"]["evidence_ids"] = ["event.lateral.999"]
        bad_result = dict(self.single_result, analysis=bad_output)
        report = self._single_report(bad_result, self.single_context)
        self.assertIsNone(report["ai_section"])
        pdf = generate_analysis_report_pdf(report)
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_batch_comparison_ai_is_written_only_to_comparison_pdf(self):
        rows_a = [sample("2026-10-01T00:00:00+00:00", 20, 0.1, 0.2), sample("2026-10-01T00:00:01+00:00", 25, 0.2, 0.1)]
        rows_b = [sample("2026-10-02T00:00:00+00:00", 30, 0.3, 0.2), sample("2026-10-02T00:00:02+00:00", 35, 0.4, 0.5)]
        comparisons = compare_run_batches([
            {"batch_id": 1, "label": "batch A", "samples": rows_a},
            {"batch_id": 2, "label": "batch B", "samples": rows_b},
        ])
        context = build_batch_comparison_payload(comparisons)
        selection = {"vehicle_id": 5, "batch_ids": [1, 2]}
        ai_result = self._success_result(context, selection)
        report = build_batch_comparison_report_data(
            comparisons, context, ai_result, selection_fingerprint(selection),
            generated_at=datetime(2026, 10, 8, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(report["ai_section"]["analysis_type"], "batch_comparison")
        pdf, text = self._generate_and_read_story(generate_batch_comparison_report_pdf, report)
        self.assertIn("批次对比报告", text)
        self.assertIn("当前分析显示已计算的速度统计。", text)
        self.assertIn("batch.B1.speed.mean", text)
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_single_batch_ai_output_cannot_be_attached_to_comparison_report(self):
        comparisons = compare_run_batches([
            {"batch_id": 1, "label": "A", "samples": self.samples},
            {"batch_id": 2, "label": "B", "samples": self.samples},
        ])
        context = build_batch_comparison_payload(comparisons)
        report = build_batch_comparison_report_data(comparisons, context, self.single_result)
        self.assertIsNone(report["ai_section"])
        self.assertTrue(generate_batch_comparison_report_pdf(report).startswith(b"%PDF-"))

    def test_provider_and_model_are_allowlisted_and_api_key_field_is_never_copied(self):
        report = self._single_report(self.single_result, self.single_context)
        self.assertEqual(report["ai_section"]["provider"], "openai")
        self.assertEqual(report["ai_section"]["model"], "configured-test-model")
        self.assertNotIn("api_key", report["ai_section"])
        self.assertNotIn("SHOULD_NEVER-APPEAR", repr(report["ai_section"]))


if __name__ == "__main__":
    unittest.main()
