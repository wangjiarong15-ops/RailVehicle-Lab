"""Mock-only tests for optional OpenAI Responses provider configuration."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.context import build_single_batch_payload
from rail_vehicle.ai.openai_provider import (
    MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    OpenAIResponsesProvider,
    _analysis_output_schema,
)
from rail_vehicle.ai.provider import AIAnalysisService, FakeProvider
from rail_vehicle.ai.prompts import (
    build_evidence_reference_instructions,
    build_output_contract_instructions,
)
from rail_vehicle.ai.schemas import collect_evidence_ids, collect_evidence_metadata
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
        "summary": {"text": "观测到已计算的运行指标。", "evidence_ids": ["metric.speed.mean"], "claim_scope": {"category": "metric", "axis": "not_applicable", "batch_refs": ["B1"]}},
        "observations": [],
        "possible_causes": [],
        "further_checks": [],
        "limitations": [],
    }


class MockResponses:
    def __init__(self, output_text=None, error=None, response_status="completed", incomplete_details=None):
        self.output_text = output_text
        self.error = error
        self.response_status = response_status
        self.incomplete_details = incomplete_details
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return types.SimpleNamespace(
            output_text=self.output_text,
            status=self.response_status,
            incomplete_details=self.incomplete_details,
            output=[],
        )


class OpenAIProviderTests(unittest.TestCase):
    @staticmethod
    def _walk_schema_nodes(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from OpenAIProviderTests._walk_schema_nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from OpenAIProviderTests._walk_schema_nodes(child)

    def test_strict_schema_objects_have_unique_complete_required_fields(self):
        schema = _analysis_output_schema(strict_compatible=True)
        object_nodes = [
            node for node in self._walk_schema_nodes(schema)
            if node.get("type") == "object"
        ]

        self.assertTrue(object_nodes)
        for node in object_nodes:
            with self.subTest(properties=list(node.get("properties", {}))):
                properties = node.get("properties", {})
                required = node.get("required", [])
                self.assertEqual(len(required), len(dict.fromkeys(required)))
                for field in required:
                    self.assertIn(field, properties)
                for field in properties:
                    self.assertIn(field, required)
                self.assertIs(node.get("additionalProperties"), False)

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
        self.assertEqual(responses.calls[0]["max_output_tokens"], 3000)
        self.assertFalse(responses.calls[0]["store"])
        output_format = responses.calls[0]["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertTrue(output_format["strict"])
        output_schema = output_format["schema"]
        summary_schema = output_schema["properties"]["summary"]
        self.assertEqual(set(summary_schema["properties"]), set(summary_schema["required"]))
        self.assertEqual(summary_schema["properties"]["claim_scope"]["type"], "object")
        self.assertIn("claim_scope", summary_schema["required"])
        for node in (
            output_schema["properties"]["summary"],
            output_schema["properties"]["observations"]["items"],
            output_schema["properties"]["possible_causes"]["items"],
            output_schema["properties"]["further_checks"]["items"],
            output_schema["properties"]["limitations"]["items"],
        ):
            self.assertIn("claim_scope", node["required"])
            self.assertEqual(node["properties"]["claim_scope"]["type"], "object")
        prompt = "".join(
            item["text"]
            for message in responses.calls[0]["input"]
            for item in message["content"]
        )
        shared_evidence_rules = build_evidence_reference_instructions(context)
        self.assertIn(shared_evidence_rules, prompt)
        self.assertIn(build_output_contract_instructions(), prompt)
        self.assertIn(json.dumps(sorted(collect_evidence_ids(context)), ensure_ascii=False, separators=(",", ":")), prompt)
        self.assertIn(
            json.dumps(collect_evidence_metadata(context), ensure_ascii=False, separators=(",", ":")),
            prompt,
        )
        self.assertIn("不得创造、猜测、改写", prompt)
        self.assertIn("claim_scope.category", prompt)
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
        self.assertEqual(result["code"], "output_json_invalid")
        self.assertNotIn("not json", result["message"])

    def test_incomplete_partial_json_is_classified_before_json_parsing(self):
        partial_text = '{"summary":{"text":"PRIVATE_PARTIAL_OUTPUT'
        provider = OpenAIResponsesProvider(
            AIConfig(enabled=True, model="m", api_key="configured"),
            client=types.SimpleNamespace(responses=MockResponses(
                output_text=partial_text,
                response_status="incomplete",
                incomplete_details=types.SimpleNamespace(reason="max_output_tokens"),
            )),
        )
        result = AIAnalysisService(provider).analyze(payload())

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "incomplete_response")
        self.assertEqual(result["diagnostics"]["status"], "incomplete")
        self.assertEqual(result["diagnostics"]["incomplete_reason"], "max_output_tokens")
        self.assertEqual(result["diagnostics"]["output_text_length"], len(partial_text))
        self.assertNotIn("analysis", result)
        self.assertNotIn("PRIVATE_PARTIAL_OUTPUT", json.dumps(result))

    def test_in_progress_and_failed_responses_are_not_parsed_as_json(self):
        for status, expected_code in (("in_progress", "response_in_progress"), ("failed", "response_failed")):
            with self.subTest(status=status):
                provider = OpenAIResponsesProvider(
                    AIConfig(enabled=True, model="m", api_key="configured"),
                    client=types.SimpleNamespace(responses=MockResponses(
                        output_text="PRIVATE_PARTIAL_TEXT",
                        response_status=status,
                    )),
                )
                result = AIAnalysisService(provider).analyze(payload())
                self.assertEqual(result["code"], expected_code)
                self.assertNotIn("analysis", result)
                self.assertNotIn("PRIVATE_PARTIAL_TEXT", json.dumps(result))

    def test_illegal_evidence_reference_from_model_is_rejected(self):
        output = valid_output(payload())
        output["summary"]["evidence_ids"] = ["event.lateral.999"]
        provider = OpenAIResponsesProvider(
            AIConfig(enabled=True, model="m", api_key="configured"),
            client=types.SimpleNamespace(responses=MockResponses(output_text=json.dumps(output))),
        )
        result = AIAnalysisService(provider).analyze(payload())
        self.assertEqual(result["code"], "evidence_id_unknown")

    def test_structured_json_with_schema_mismatch_is_rejected(self):
        malformed = {"summary": {"text": "missing required lists"}}
        provider = OpenAIResponsesProvider(
            AIConfig(enabled=True, model="m", api_key="configured"),
            client=types.SimpleNamespace(responses=MockResponses(output_text=json.dumps(malformed))),
        )
        result = AIAnalysisService(provider).analyze(payload())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["code"], "output_schema_invalid")

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
