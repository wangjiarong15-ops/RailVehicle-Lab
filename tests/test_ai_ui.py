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
from rail_vehicle.ai.schemas import validate_output
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.dynamics import analyze_run_data


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
    return {
        "summary": {"text": "速度统计结果已计算。", "evidence_ids": [evidence]},
        "observations": [{"text": "当前样本包含速度测量。", "evidence_ids": [evidence]}],
        "possible_causes": [{"text": "可能与运行工况变化有关。", "evidence_ids": [evidence], "qualification": "hypothesis"}],
        "further_checks": [{"action": "核对同时间段的运行记录。", "reason": "确认该统计表现是否持续出现。", "evidence_ids": [evidence]}],
        "limitations": [{"text": "结论仅基于提供的统计指标。", "evidence_ids": [evidence]}],
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
        bad = valid_response(single_payload())
        bad["summary"]["evidence_ids"] = ["metric.unknown"]
        result = request_ai_analysis(single_payload(), self.config, has_data=True, provider_factory=lambda _: FakeProvider(bad))
        self.assertEqual(result["status"], "validation_failed")
        self.assertIn("未通过", status_message(result))

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
