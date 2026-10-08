"""Mock-only tests for optional OpenAI Responses provider configuration."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.context import build_single_batch_payload
from rail_vehicle.ai.openai_provider import MAX_OUTPUT_TOKENS, MAX_RETRIES, OpenAIResponsesProvider
from rail_vehicle.ai.provider import AIAnalysisService, FakeProvider
from rail_vehicle.ai.schemas import collect_evidence_ids
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.dynamics import analyze_run_data


def samples():
    return [
        {"timestamp": "2026-10-08T10:00:00+00:00", "speed_kmh": 30, "lateral_accel": 0.2, "vertical_accel": 0.1},
        {"timestamp": "2026-10-08T10:00:01+00:00", "speed_kmh": 35, "lateral_accel": 1.4, "vertical_accel": -0.2},
    ]


def payload():
    rows = samples()
    return build_single_batch_payload(
        analysis=analyze_run_data(rows, 1.0),
        anomalies=detect_anomalies(rows, 1.0),
        vehicle={"vehicle_type": "测试车型", "mass_kg": 100000, "axle_count": 8, "bogie_count": 4, "max_speed_kmh": 160},
        batch_ref="B1",
        selected_start=rows[0]["timestamp"],
        selected_end=rows[-1]["timestamp"],
    )


def valid_output(context):
    return {
        "summary": {"text": "观测到已计算的运行指标。", "evidence_ids": ["metric.speed.mean"]},
        "observations": [],
        "possible_causes": [],
        "further_checks": [],
        "limitations": [],
    }


class MockResponses:
    def __init__(self, output_text=None, error=None):
        self.output_text = output_text
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return types.SimpleNamespace(output_text=self.output_text)


class OpenAIProviderTests(unittest.TestCase):
    def test_configuration_reads_secrets_and_environment_with_secret_precedence(self):
        config = AIConfig.from_sources(
            secrets={"AI_MODEL": "secret-model", "OPENAI_API_KEY": "secret-key"},
            environ={"AI_MODEL": "env-model", "AI_ENABLED": "true", "AI_TIMEOUT": "17", "OPENAI_API_KEY": "env-key"},
        )
        self.assertEqual(config.model, "secret-model")
        self.assertEqual(config.api_key, "secret-key")
        self.assertTrue(config.enabled)
        self.assertEqual(config.timeout, 17)

    def test_api_key_can_be_read_from_streamlit_ai_section(self):
        config = AIConfig.from_sources(secrets={"ai": {"api_key": "nested-secret"}}, environ={})
        self.assertEqual(config.api_key, "nested-secret")

    def test_ai_enabled_defaults_to_false_and_timeout_is_bounded(self):
        config = AIConfig.from_sources(secrets={}, environ={"AI_TIMEOUT": "900"})
        self.assertFalse(config.enabled)
        self.assertEqual(config.timeout, 30.0)

    def test_disabled_provider_returns_unavailable_without_needing_key_or_sdk(self):
        config = AIConfig(enabled=False, model="model", api_key=None)
        result = AIAnalysisService(OpenAIResponsesProvider(config)).analyze(payload())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "disabled")

    def test_missing_api_key_returns_clear_unavailable_status(self):
        config = AIConfig(enabled=True, model="configured-model", api_key=None)
        result = AIAnalysisService(OpenAIResponsesProvider(config)).analyze(payload())
        self.assertEqual(result["code"], "missing_api_key")
        self.assertIn("未配置", result["message"])

    def test_mocked_responses_api_flow_uses_configured_model_schema_limits_and_sanitized_input(self):
        context = payload()
        response_text = json.dumps(valid_output(context), ensure_ascii=False)
        responses = MockResponses(output_text=response_text)
        provider = OpenAIResponsesProvider(
            AIConfig(provider="openai", enabled=True, model="configured-model-x", timeout=9, api_key="never-log-this"),
            client=types.SimpleNamespace(responses=responses),
        )
        result = AIAnalysisService(provider).analyze(context)

        self.assertEqual(result["status"], "available")
        self.assertEqual(responses.calls[0]["model"], "configured-model-x")
        self.assertEqual(responses.calls[0]["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertFalse(responses.calls[0]["store"])
        output_format = responses.calls[0]["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertTrue(output_format["strict"])
        self.assertIn("metric.speed.mean", collect_evidence_ids(context))
        encoded_request = json.dumps(responses.calls[0], ensure_ascii=False)
        self.assertNotIn("never-log-this", encoded_request)
        self.assertNotIn("vehicle_code", encoded_request)
        self.assertNotIn("\"samples\":", encoded_request)

    def test_sdk_client_configuration_sets_timeout_and_finite_retries(self):
        constructed = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                constructed.update(kwargs)

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
            client = OpenAIResponsesProvider._build_client(
                AIConfig(enabled=True, model="m", timeout=11, api_key="test-only-secret")
            )
        self.assertIsInstance(client, FakeOpenAI)
        self.assertEqual(constructed["timeout"], 11)
        self.assertEqual(constructed["max_retries"], MAX_RETRIES)
        self.assertEqual(MAX_RETRIES, 1)

    def test_timeout_network_authentication_and_rate_limit_errors_are_safely_mapped(self):
        error_cases = [
            ("APITimeoutError", "timeout"),
            ("APIConnectionError", "network"),
            ("AuthenticationError", "authentication"),
            ("RateLimitError", "rate_limit"),
        ]
        for exception_name, expected_code in error_cases:
            with self.subTest(exception=exception_name):
                error_type = type(exception_name, (RuntimeError,), {})
                responses = MockResponses(error=error_type("secret-key must not appear"))
                provider = OpenAIResponsesProvider(
                    AIConfig(enabled=True, model="m", api_key="configured"),
                    client=types.SimpleNamespace(responses=responses),
                )
                result = AIAnalysisService(provider).analyze(payload())
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["code"], expected_code)
                self.assertNotIn("secret-key", result["message"])

    def test_invalid_model_json_is_not_exposed(self):
        provider = OpenAIResponsesProvider(
            AIConfig(enabled=True, model="m", api_key="configured"),
            client=types.SimpleNamespace(responses=MockResponses(output_text="not json")),
        )
        result = AIAnalysisService(provider).analyze(payload())
        self.assertEqual(result["code"], "invalid_model_output")
        self.assertNotIn("not json", result["message"])

    def test_illegal_evidence_reference_from_model_is_rejected(self):
        output = valid_output(payload())
        output["summary"]["evidence_ids"] = ["event.lateral.999"]
        provider = OpenAIResponsesProvider(
            AIConfig(enabled=True, model="m", api_key="configured"),
            client=types.SimpleNamespace(responses=MockResponses(output_text=json.dumps(output))),
        )
        result = AIAnalysisService(provider).analyze(payload())
        self.assertEqual(result["code"], "invalid_model_output")

    def test_structured_json_with_schema_mismatch_is_rejected(self):
        malformed = {"summary": {"text": "missing required lists"}}
        provider = OpenAIResponsesProvider(
            AIConfig(enabled=True, model="m", api_key="configured"),
            client=types.SimpleNamespace(responses=MockResponses(output_text=json.dumps(malformed))),
        )
        result = AIAnalysisService(provider).analyze(payload())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "invalid_model_output")

    def test_invalid_response_shape_is_reported_as_unavailable(self):
        provider = OpenAIResponsesProvider(
            AIConfig(enabled=True, model="m", api_key="configured"),
            client=types.SimpleNamespace(responses=MockResponses(output_text=None)),
        )
        result = AIAnalysisService(provider).analyze(payload())
        self.assertEqual(result["code"], "invalid_response")

    def test_provider_unavailability_does_not_disable_local_statistics_and_detection(self):
        rows = samples()
        failed = AIAnalysisService(OpenAIResponsesProvider(AIConfig(enabled=False))).analyze(payload())
        stats = analyze_run_data(rows, 1.0)
        detections = detect_anomalies(rows, 1.0)
        comparison = compare_run_batches([
            {"batch_id": 1, "label": "one", "samples": rows},
            {"batch_id": 2, "label": "two", "samples": rows},
        ])
        self.assertEqual(failed["status"], "unavailable")
        self.assertEqual(stats["sample_count"], 2)
        self.assertEqual(len(detections["events"]), 1)
        self.assertEqual(len(comparison), 2)

    def test_fake_provider_remains_usable_with_common_service(self):
        context = payload()
        result = AIAnalysisService(FakeProvider(valid_output(context))).analyze(context)
        self.assertEqual(result["status"], "available")


if __name__ == "__main__":
    unittest.main()
