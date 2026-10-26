"""Page-integration helpers: rendering, opt-in dispatch, and cache invalidation."""

import json
import types
import unittest

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.context import build_batch_comparison_payload, build_single_batch_payload
from rail_vehicle.ai.openai_provider import OpenAIResponsesProvider
from rail_vehicle.ai.provider import FakeProvider, input_fingerprint
from rail_vehicle.ai.ui import (
    invalidate_stale_result,
    render_validated_result,
    request_ai_analysis,
    selection_fingerprint,
    status_message,
)
from rail_vehicle.ai.schemas import collect_evidence_metadata, validate_output
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.pages.run_analysis import _safe_pdf_error_message


def sample(timestamp, speed, lateral, vertical):
    return {"timestamp": timestamp, "speed_kmh": speed, "lateral_accel": lateral, "vertical_accel": vertical}


def single_payload():
    rows = [sample("2026-10-08T12:00:00+00:00", 25, 0.1, 0.2), sample("2026-10-08T12:00:01+00:00", 28, 1.5, -0.1)]
    return build_single_batch_payload(
        analysis=analyze_run_data(rows, 1),
        anomalies=detect_anomalies(rows, 1),
        vehicle={"vehicle_type": "测试车型", "mass_kg": 90000, "axle_count": 8, "bogie_count": 4, "max_speed_kmh": 160},
        batch_ref="B1",
        selected_start=rows[0]["timestamp"],
        selected_end=rows[-1]["timestamp"],
    )


def comparison_payload():
    rows1 = [sample("2026-10-01T00:00:00+00:00", 20, 0.1, 0.2), sample("2026-10-01T00:00:01+00:00", 25, 0.3, 0.1)]
    rows2 = [sample("2026-10-02T00:00:00+00:00", 30, 0.5, 0.2), sample("2026-10-02T00:00:02+00:00", 35, 0.2, 0.4)]
    comparisons = compare_run_batches([
        {"batch_id": 10, "label": "private label 1", "samples": rows1},
        {"batch_id": 11, "label": "private label 2", "samples": rows2},
    ])
    return build_batch_comparison_payload(comparisons)


def valid_response(payload):
    evidence = "metric.speed.mean" if payload["analysis_type"] == "single_batch" else "batch.B1.speed.mean"
    metadata = {item["evidence_id"]: item for item in collect_evidence_metadata(payload)}[evidence]
    scope = {
        "category": metadata["category"],
        "axis": metadata["axis"],
        "batch_refs": metadata["batch_refs"],
    }
    return {
        "summary": {"text": "速度统计结果已计算。", "evidence_ids": [evidence], "claim_scope": scope},
        "observations": [{"text": "当前样本包含速度测量。", "evidence_ids": [evidence], "claim_scope": scope}],
        "possible_causes": [{"text": "可能与运行工况变化有关。", "evidence_ids": [evidence], "qualification": "hypothesis", "claim_scope": scope}],
        "further_checks": [{"action": "核对同时间段的运行记录。", "reason": "确认该统计表现是否持续出现。", "evidence_ids": [evidence], "claim_scope": scope}],
        "limitations": [{"text": "结论仅基于提供的统计指标。", "evidence_ids": [evidence], "claim_scope": scope}],
    }


class RenderRecorder:
    def __init__(self):
        self.calls = []

    def _record(self, name, value):
        self.calls.append((name, value))

    def markdown(self, value): self._record("markdown", value)
    def text(self, value): self._record("text", value)
    def caption(self, value): self._record("caption", value)
    def error(self, value): self._record("error", value)
    def warning(self, value): self._record("warning", value)

    def expander(self, value, **kwargs):
        from contextlib import nullcontext
        self._record("expander", value)
        return nullcontext()

    def dataframe(self, value, **kwargs): self._record("dataframe", value)


class AIUIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = AIConfig(enabled=True, model="mock-model", api_key="test-only-placeholder")

    def test_success_result_renders_all_sections_and_visible_evidence_ids(self):
        context = single_payload()
        output = valid_response(context)
        result = {"analysis": validate_output(output, context), "input_fingerprint": input_fingerprint(context)}
        recorder = RenderRecorder()
        self.assertTrue(render_validated_result(result, context, recorder))
        rendered = "\n".join(str(value) for _, value in recorder.calls)
        self.assertIn("核心结论", rendered)
        self.assertIn("主要观察", rendered)
        self.assertIn("可能原因", rendered)
        self.assertIn("待核实的假设：hypothesis", rendered)
        self.assertIn("建议：核对同时间段的运行记录。", rendered)
        self.assertIn("依据：确认该统计表现是否持续出现。", rendered)
        self.assertIn("局限性", rendered)
        self.assertIn("metric.speed.mean", rendered)

    def test_ai_disabled_does_not_call_provider(self):
        calls = []
        result = request_ai_analysis(single_payload(), AIConfig(enabled=False), has_data=True, provider_factory=lambda config: calls.append(config))
        self.assertEqual(result["code"], "disabled")
        self.assertEqual(calls, [])

    def test_missing_api_key_does_not_call_provider(self):
        calls = []
        result = request_ai_analysis(
            single_payload(), AIConfig(enabled=True, model="m", api_key=None), has_data=True,
            provider_factory=lambda config: calls.append(config),
        )
        self.assertEqual(result["code"], "missing_api_key")
        self.assertEqual(calls, [])

    def test_unavailable_provider_returns_user_friendly_message(self):
        class MockResponses:
            def create(self, **kwargs):
                raise type("APIConnectionError", (RuntimeError,), {})("do not expose raw error")

        result = request_ai_analysis(
            single_payload(), self.config, has_data=True,
            provider_factory=lambda config: OpenAIResponsesProvider(
                config, client=types.SimpleNamespace(responses=MockResponses())
            ),
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("暂时不可用", status_message(result))
        self.assertNotIn("raw error", status_message(result))

    def test_validation_failed_is_a_distinct_safe_state(self):
        context = single_payload()
        bad = valid_response(context)
        bad["summary"]["text"] = "PRIVATE_MODEL_OUTPUT"
        bad["summary"]["evidence_ids"] = ["metric.unknown"]
        result = request_ai_analysis(context, self.config, has_data=True, provider_factory=lambda _: FakeProvider(bad))
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["code"], "evidence_id_unknown")
        rendered_status = status_message(result)
        self.assertIn("未知证据", rendered_status)
        self.assertNotIn("PRIVATE_MODEL_OUTPUT", rendered_status)
        self.assertNotIn("metric.unknown", rendered_status)
        recorder = RenderRecorder()
        self.assertFalse(render_validated_result(result, context, recorder))
        rendered = json.dumps(recorder.calls, ensure_ascii=False)
        self.assertNotIn("PRIVATE_MODEL_OUTPUT", rendered)
        self.assertNotIn("metric.unknown", rendered)

    def test_validation_error_codes_map_to_safe_user_messages(self):
        messages = {
            "output_json_invalid": "AI 返回文本不是有效 JSON，未展示结果。",
            "output_schema_invalid": "AI 返回内容不符合输出结构要求，未展示结果。",
            "claim_scope_schema_invalid": "AI 输出的证据范围格式无效，未展示结果。",
            "evidence_id_unknown": "AI 输出引用了未知证据，未展示结果。",
            "evidence_scope_category_mismatch": "AI 输出的证据类别与引用不匹配，未展示结果。",
            "evidence_scope_axis_mismatch": "AI 输出的证据轴向与引用不匹配，未展示结果。",
            "evidence_scope_batch_mismatch": "AI 输出的批次范围与引用不匹配，未展示结果。",
            "unsafe_claim_text": "AI 返回内容包含不允许的结论措辞，未展示结果。",
            "output_contract_invalid": "AI 返回内容未通过校验，已阻止展示。",
        }
        for code, expected in messages.items():
            with self.subTest(code=code):
                message = status_message({
                    "status": "validation_failed",
                    "code": code,
                    "message": "PRIVATE_RAW_VALIDATOR_DETAILS",
                })
                self.assertEqual(message, expected)
                self.assertNotIn("PRIVATE_RAW_VALIDATOR_DETAILS", message)

    def test_incomplete_response_has_safe_message_and_diagnostics_without_partial_text(self):
        private_text = "PRIVATE_PARTIAL_JSON"
        response = {
            "status": "unavailable",
            "code": "incomplete_response",
            "message": "private raw details must not be used",
            "diagnostics": {
                "status": "incomplete",
                "incomplete_reason": "max_output_tokens",
                "has_message": True,
                "has_output_text": True,
                "output_text_length": len(private_text),
                "max_output_tokens_limit": 3000,
                "usage_output_tokens": 3000,
                "reasoning_tokens": "not-a-number",
                "has_refusal": False,
            },
        }
        message = status_message(response)
        self.assertIn("未完整生成", message)
        self.assertIn("incomplete_details.reason=max_output_tokens", message)
        self.assertIn("reasoning_tokens=unknown", message)
        self.assertNotIn(private_text, message)
        self.assertNotIn("private raw details", message)

    def test_pdf_error_messages_do_not_expose_exception_details(self):
        private_detail = "PRIVATE_LOCAL_PATH_OR_INTERNAL_ERROR"
        error = RuntimeError(private_detail)
        messages = (
            _safe_pdf_error_message("single", error),
            _safe_pdf_error_message("comparison", error),
            _safe_pdf_error_message("unknown", error),
        )
        self.assertIn("分析报告生成失败", messages[0])
        self.assertIn("批次对比报告生成失败", messages[1])
        self.assertIn("PDF 报告生成失败", messages[2])
        self.assertTrue(all(private_detail not in message for message in messages))

    def test_rejected_model_text_and_exception_details_never_reach_ui(self):
        private_marker = "PRIVATE_MODEL_BODY_OR_EXCEPTION"

        class RaisingProvider:
            def analyze(self, payload):
                from rail_vehicle.ai.schemas import AIContractError

                raise AIContractError(private_marker, code="unknown-internal-code")

        result = request_ai_analysis(
            single_payload(), self.config, has_data=True,
            provider_factory=lambda _: RaisingProvider(),
        )
        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(result["code"], "output_contract_invalid")
        status = status_message(result)
        recorder = RenderRecorder()
        self.assertFalse(render_validated_result(result, single_payload(), recorder))
        visible = json.dumps({"result": result, "status": status, "rendered": recorder.calls}, ensure_ascii=False)
        self.assertNotIn(private_marker, visible)
        self.assertNotIn("unknown-internal-code", visible)
        self.assertNotIn("test-only-placeholder", visible)

    def test_invalid_evidence_reference_is_never_rendered(self):
        context = single_payload()
        bad = valid_response(context)
        bad["summary"]["text"] = "不应出现的旧结论"
        bad["summary"]["evidence_ids"] = ["event.lateral.999"]
        recorder = RenderRecorder()
        self.assertFalse(render_validated_result({"analysis": bad, "input_fingerprint": input_fingerprint(context)}, context, recorder))
        rendered = "\n".join(str(value) for _, value in recorder.calls)
        self.assertIn("校验失败", rendered)
        self.assertNotIn("不应出现的旧结论", rendered)

    def test_legacy_missing_or_null_scope_is_rendered_with_unverified_warning(self):
        context = single_payload()
        categories = ("summary", "observations", "possible_causes", "further_checks", "limitations")
        for category in categories:
            for scope_state in ("missing", "null"):
                with self.subTest(category=category, scope_state=scope_state):
                    output = valid_response(context)
                    target = output[category] if category == "summary" else output[category][0]
                    if scope_state == "missing":
                        target.pop("claim_scope")
                    else:
                        target["claim_scope"] = None
                    recorder = RenderRecorder()
                    legacy_result = {"analysis": output, "input_fingerprint": input_fingerprint(context)}
                    self.assertTrue(render_validated_result(legacy_result, context, recorder))
                    rendered = "\n".join(str(value) for _, value in recorder.calls)
                    self.assertIn("证据范围未验证", rendered)
                    self.assertIn("类别、轴向和批次范围未核对", rendered)

    def test_new_result_marked_verified_cannot_use_legacy_missing_scope(self):
        context = single_payload()
        output = valid_response(context)
        output["summary"].pop("claim_scope")
        recorder = RenderRecorder()
        new_result = {
            "analysis": output,
            "input_fingerprint": input_fingerprint(context),
            "scope_validation": "verified",
        }
        self.assertFalse(render_validated_result(new_result, context, recorder))
        self.assertNotIn("速度统计结果已计算。", "\n".join(str(value) for _, value in recorder.calls))

    def test_stale_result_is_invalidated_when_context_or_selection_changes(self):
        context = single_payload()
        selection = {"vehicle_id": 1, "batch_id": 3, "threshold": 1.0}
        cache = {"result": {"input_fingerprint": input_fingerprint(context), "selection_fingerprint": selection_fingerprint(selection)}}
        changed_context = dict(context)
        changed_context["threshold"] = dict(context["threshold"], value=2.0)
        self.assertTrue(invalidate_stale_result(cache, "result", changed_context, selection))
        self.assertNotIn("result", cache)

        cache["result"] = {"input_fingerprint": input_fingerprint(context), "selection_fingerprint": selection_fingerprint(selection)}
        changed_selection = dict(selection, batch_id=4)
        self.assertTrue(invalidate_stale_result(cache, "result", context, changed_selection))
        self.assertNotIn("result", cache)

    def test_single_batch_context_is_forwarded_unchanged(self):
        context = single_payload()
        provider = FakeProvider(valid_response(context))
        result = request_ai_analysis(context, self.config, has_data=True, provider_factory=lambda _: provider)
        self.assertEqual(provider.last_payload["analysis_type"], "single_batch")
        self.assertEqual(provider.last_payload, context)
        self.assertEqual(result["status"], "available")

    def test_multi_batch_context_is_forwarded_unchanged(self):
        context = comparison_payload()
        provider = FakeProvider(valid_response(context))
        result = request_ai_analysis(context, self.config, has_data=True, provider_factory=lambda _: provider)
        self.assertEqual(provider.last_payload["analysis_type"], "batch_comparison")
        self.assertEqual(provider.last_payload, context)
        self.assertEqual(result["status"], "available")

    def test_no_data_prevents_provider_call(self):
        provider_calls = []
        result = request_ai_analysis(
            single_payload(), self.config, has_data=False,
            provider_factory=lambda config: provider_calls.append(config),
        )
        self.assertEqual(result["code"], "no_data")
        self.assertEqual(provider_calls, [])


if __name__ == "__main__":
    unittest.main()
