"""Mock-only coverage for the DeepSeek Responses API adapter."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from rail_vehicle.ai.config import AIConfig, DEEPSEEK_DEFAULT_BASE_URL
from rail_vehicle.ai.deepseek_provider import (
    DEFAULT_DEEPSEEK_MODEL,
    MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    DeepSeekResponsesProvider,
)
from rail_vehicle.ai.openai_provider import create_configured_provider
from rail_vehicle.ai.provider import AIAnalysisService, AIProviderUnavailable
from rail_vehicle.ai.schemas import collect_evidence_ids
from rail_vehicle.ai.ui import ai_configuration_message, status_message
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.dynamics import analyze_run_data


def build_payload():
    samples = [
        {"timestamp": "2026-10-08T10:00:00+00:00", "speed_kmh": 30, "lateral_accel": 0.2, "vertical_accel": 0.1},
        {"timestamp": "2026-10-08T10:00:01+00:00", "speed_kmh": 35, "lateral_accel": 1.4, "vertical_accel": -0.2},
    ]
    return build_single_payload_from_samples(samples)


def build_single_payload_from_samples(samples):
    from rail_vehicle.ai.context import build_single_batch_payload

    analysis = analyze_run_data(samples, 1.0)
    anomalies = detect_anomalies(analysis["samples"], 1.0)
    return build_single_batch_payload(
        analysis=analysis,
        anomalies=anomalies,
        vehicle={
            "vehicle_code": "PRIVATE-LOCAL-ID",
            "vehicle_type": "测试车型",
            "mass_kg": 100000,
            "axle_count": 8,
            "bogie_count": 4,
            "max_speed_kmh": 160,
            "notes": "PRIVATE-LOCAL-NOTE",
        },
        batch_ref="B1",
        selected_start=samples[0]["timestamp"],
        selected_end=samples[-1]["timestamp"],
    )


def valid_output(context):
    evidence_id = "metric.speed.mean"
    assert evidence_id in collect_evidence_ids(context)
    return {
        "summary": {"text": "观测到已计算的运行指标。", "evidence_ids": [evidence_id]},
        "observations": [],
        "possible_causes": [],
        "further_checks": [],
        "limitations": [],
    }


class MockResponses:
    def __init__(self, output_text=None, error=None, response=None):
        self.output_text = output_text
        self.error = error
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if self.response is not None:
            return self.response
        return types.SimpleNamespace(status="completed", output_text=self.output_text)


class DeepSeekConfigurationTests(unittest.TestCase):
    def test_deepseek_environment_selects_default_model_endpoint_and_key(self):
        config = AIConfig.from_sources(
            secrets={},
            environ={
                "AI_PROVIDER": "deepseek",
                "AI_ENABLED": "true",
                "DEEPSEEK_API_KEY": "test-only-deepseek-placeholder",
            },
        )

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.model, DEFAULT_DEEPSEEK_MODEL)
        self.assertEqual(config.base_url, DEEPSEEK_DEFAULT_BASE_URL)
        self.assertEqual(config.api_key, "test-only-deepseek-placeholder")
        self.assertIsNone(ai_configuration_message(config))

    def test_deepseek_model_and_base_url_can_be_overridden_and_secrets_take_precedence(self):
        config = AIConfig.from_sources(
            secrets={
                "AI_PROVIDER": "deepseek",
                "AI_MODEL": "secret-configured-model",
                "DEEPSEEK_API_KEY": "test-only-secret-placeholder",
                "DEEPSEEK_BASE_URL": "https://proxy.example.invalid/v1",
            },
            environ={
                "AI_PROVIDER": "openai",
                "AI_MODEL": "ignored-model",
                "DEEPSEEK_API_KEY": "ignored-key",
                "DEEPSEEK_BASE_URL": "https://ignored.example.invalid",
            },
        )

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.model, "secret-configured-model")
        self.assertEqual(config.api_key, "test-only-secret-placeholder")
        self.assertEqual(config.base_url, "https://proxy.example.invalid/v1")

    def test_deepseek_key_is_supported_in_streamlit_secrets_sections(self):
        config = AIConfig.from_sources(
            secrets={"deepseek": {"api_key": "test-only-deepseek-placeholder", "base_url": "https://proxy.invalid"}},
            environ={"AI_PROVIDER": "deepseek"},
        )
        self.assertEqual(config.api_key, "test-only-deepseek-placeholder")
        self.assertEqual(config.base_url, "https://proxy.invalid")

    def test_openai_configuration_and_key_lookup_remain_unchanged(self):
        config = AIConfig.from_sources(
            secrets={},
            environ={"AI_PROVIDER": "openai", "AI_MODEL": "openai-test-model", "OPENAI_API_KEY": "test-only-openai-placeholder"},
        )
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.model, "openai-test-model")
        self.assertEqual(config.api_key, "test-only-openai-placeholder")
        self.assertIsNone(config.base_url)

    def test_configured_factory_selects_provider_without_making_a_request(self):
        provider = create_configured_provider(
            AIConfig(provider="deepseek", enabled=True, api_key="test-only-placeholder")
        )
        self.assertIsInstance(provider, DeepSeekResponsesProvider)


class DeepSeekResponsesProviderTests(unittest.TestCase):
    def setUp(self):
        self.context = build_payload()
        self.responses = MockResponses(output_text=json.dumps(valid_output(self.context), ensure_ascii=False))
        self.config = AIConfig(
            provider="deepseek",
            model="deepseek-test-model",
            enabled=True,
            timeout=13,
            api_key="test-only-deepseek-placeholder",
        )
        self.provider = DeepSeekResponsesProvider(
            self.config,
            client=types.SimpleNamespace(responses=self.responses),
        )

    def test_request_uses_responses_json_schema_and_only_preassembled_context(self):
        result = AIAnalysisService(self.provider).analyze(self.context)

        self.assertEqual(result["status"], "available")
        request = self.responses.calls[0]
        self.assertEqual(request["model"], "deepseek-test-model")
        self.assertEqual(request["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertNotIn("store", request)
        output_format = request["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertEqual(output_format["name"], "rail_vehicle_analysis")
        self.assertIn("schema", output_format)
        self.assertNotIn("strict", output_format)
        serialized = json.dumps(request, ensure_ascii=False)
        self.assertIn("metric.speed.mean", serialized)
        self.assertNotIn("PRIVATE-LOCAL-ID", serialized)
        self.assertNotIn("PRIVATE-LOCAL-NOTE", serialized)
        self.assertNotIn("test-only-deepseek-placeholder", serialized)

    def test_build_client_sets_deepseek_endpoint_timeout_and_finite_retry(self):
        received = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                received.update(kwargs)

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
            client = DeepSeekResponsesProvider._build_client(self.config)

        self.assertIsInstance(client, FakeOpenAI)
        self.assertEqual(received["api_key"], "test-only-deepseek-placeholder")
        self.assertEqual(received["base_url"], DEEPSEEK_DEFAULT_BASE_URL)
        self.assertEqual(received["timeout"], 13)
        self.assertEqual(received["max_retries"], MAX_RETRIES)
        self.assertEqual(MAX_RETRIES, 1)

    def test_custom_base_url_is_passed_to_client(self):
        custom = AIConfig(**{**self.config.__dict__, "base_url": "https://proxy.example.invalid/v1"})
        received = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                received.update(kwargs)

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
            DeepSeekResponsesProvider._build_client(custom)
        self.assertEqual(received["base_url"], "https://proxy.example.invalid/v1")

    def test_default_model_is_used_when_provider_is_created_directly_without_model(self):
        config = AIConfig(provider="deepseek", enabled=True, api_key="test-only-placeholder")
        responses = MockResponses(output_text=json.dumps(valid_output(self.context), ensure_ascii=False))
        provider = DeepSeekResponsesProvider(config, client=types.SimpleNamespace(responses=responses))
        provider.analyze(self.context)
        self.assertEqual(responses.calls[0]["model"], DEFAULT_DEEPSEEK_MODEL)

    def test_documented_responses_shape_is_extracted_without_sdk_output_text_helper(self):
        expected_json = json.dumps(valid_output(self.context), ensure_ascii=False)
        response = {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "content": [{"type": "reasoning_text", "text": "不应作为分析输出处理"}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": expected_json}],
                },
            ],
        }
        provider = DeepSeekResponsesProvider(
            self.config,
            client=types.SimpleNamespace(
                responses=MockResponses(response=response)
            ),
        )

        result = AIAnalysisService(provider).analyze(self.context)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["analysis"], valid_output(self.context))

    def test_incomplete_or_failed_response_is_distinguished_from_bad_json(self):
        partial_text = '{"summary":'
        cases = [
            (
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"output_tokens": MAX_OUTPUT_TOKENS},
                    "output": [{
                        "type": "message",
                        "content": [{"type": "output_text", "text": partial_text}],
                    }],
                },
                "incomplete_response",
            ),
            ({"status": "in_progress", "output": []}, "incomplete_response"),
            ({"status": "failed", "output": []}, "response_failed"),
            ({"status": "completed", "output": [{"type": "reasoning", "content": []}]}, "invalid_response"),
            (
                {
                    "status": "unexpected-status",
                    "output": [{"type": "message", "content": [{
                        "type": "output_text",
                        "text": json.dumps(valid_output(self.context), ensure_ascii=False),
                    }]}],
                },
                "invalid_response",
            ),
        ]
        for response, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                provider = DeepSeekResponsesProvider(
                    self.config,
                    client=types.SimpleNamespace(
                        responses=MockResponses(response=response)
                    ),
                )
                result = AIAnalysisService(provider).analyze(self.context)
                self.assertEqual(result["code"], expected_code)
                if response.get("status") == "incomplete":
                    diagnostics = result["diagnostics"]
                    self.assertEqual(diagnostics["status"], "incomplete")
                    self.assertEqual(diagnostics["incomplete_reason"], "max_output_tokens")
                    self.assertTrue(diagnostics["has_message"])
                    self.assertTrue(diagnostics["has_output_text"])
                    self.assertEqual(diagnostics["output_text_length"], len(partial_text))
                    self.assertEqual(diagnostics["max_output_tokens_limit"], MAX_OUTPUT_TOKENS)
                    self.assertEqual(diagnostics["usage_output_tokens"], MAX_OUTPUT_TOKENS)
                    self.assertFalse(diagnostics["has_refusal"])
                    displayed = status_message(result)
                    self.assertIn("incomplete_details.reason=max_output_tokens", displayed)
                    self.assertIn(f"output_text_length={len(partial_text)}", displayed)
                    self.assertIn(f"usage_output_tokens={MAX_OUTPUT_TOKENS}", displayed)
                    self.assertNotIn(partial_text, displayed)
                elif response.get("status") == "in_progress":
                    self.assertEqual(result["diagnostics"]["status"], "in_progress")

    def test_content_filter_refusal_is_reported_without_refusal_text(self):
        refusal_text = "private refusal details must never be shown"
        response = {
            "status": "incomplete",
            "incomplete_details": {"reason": "content_filter"},
            "output": [{
                "type": "message",
                "content": [{"type": "refusal", "refusal": refusal_text}],
            }],
        }
        provider = DeepSeekResponsesProvider(
            self.config,
            client=types.SimpleNamespace(responses=MockResponses(response=response)),
        )

        result = AIAnalysisService(provider).analyze(self.context)

        self.assertEqual(result["code"], "incomplete_response")
        self.assertEqual(result["diagnostics"]["incomplete_reason"], "content_filter")
        self.assertTrue(result["diagnostics"]["has_refusal"])
        self.assertEqual(result["diagnostics"]["output_text_length"], 0)
        self.assertNotIn(refusal_text, status_message(result))

    def test_empty_output_text_block_is_distinguished_from_missing_message(self):
        response = {
            "status": "completed",
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": ""}],
            }],
        }
        provider = DeepSeekResponsesProvider(
            self.config,
            client=types.SimpleNamespace(responses=MockResponses(response=response)),
        )

        result = AIAnalysisService(provider).analyze(self.context)

        self.assertEqual(result["code"], "invalid_response")
        self.assertTrue(result["diagnostics"]["has_message"])
        self.assertTrue(result["diagnostics"]["has_output_text"])
        self.assertEqual(result["diagnostics"]["output_text_length"], 0)

    def test_ui_messages_distinguish_parse_and_business_validation_failures(self):
        parse_message = status_message({"code": "response_parse_error"})
        validation_message = status_message({"status": "validation_failed"})
        api_response_message = status_message({"code": "invalid_response"})

        self.assertIn("不是有效 JSON", parse_message)
        self.assertIn("结构与证据校验", validation_message)
        self.assertIn("没有可用的解读文本", api_response_message)
        self.assertNotEqual(parse_message, validation_message)

    def test_invalid_json_unknown_evidence_and_empty_response_are_rejected(self):
        cases = [
            ("invalid-json", "response_parse_error"),
            (json.dumps({**valid_output(self.context), "summary": {"text": "x", "evidence_ids": ["event.missing.001"]}}), "invalid_model_output"),
            (None, "invalid_response"),
        ]
        for output_text, expected_code in cases:
            with self.subTest(expected_code=expected_code, output_text=output_text):
                responses = MockResponses(output_text=output_text)
                provider = DeepSeekResponsesProvider(
                    self.config,
                    client=types.SimpleNamespace(responses=responses),
                )
                result = AIAnalysisService(provider).analyze(self.context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["code"], expected_code)

    def test_timeout_auth_network_rate_limit_and_request_errors_are_safely_mapped(self):
        cases = [
            ("APITimeoutError", "timeout"),
            ("APIConnectionError", "network"),
            ("AuthenticationError", "authentication"),
            ("RateLimitError", "rate_limit"),
            ("BadRequestError", "request_rejected"),
        ]
        for exception_name, expected_code in cases:
            with self.subTest(exception=exception_name):
                error_type = type(exception_name, (RuntimeError,), {})
                responses = MockResponses(error=error_type("test-only-key-must-not-leak"))
                provider = DeepSeekResponsesProvider(
                    self.config,
                    client=types.SimpleNamespace(responses=responses),
                )
                result = AIAnalysisService(provider).analyze(self.context)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["code"], expected_code)
                self.assertNotIn("test-only-key-must-not-leak", result["message"])

    def test_disabled_missing_key_and_wrong_provider_make_no_request(self):
        cases = [
            (AIConfig(provider="deepseek", enabled=False), "disabled"),
            (AIConfig(provider="deepseek", enabled=True, model="m"), "missing_api_key"),
            (AIConfig(provider="openai", enabled=True, model="m", api_key="test-only"), "provider"),
        ]
        for config, code in cases:
            with self.subTest(code=code):
                responses = MockResponses(output_text="unused")
                provider = DeepSeekResponsesProvider(config, client=types.SimpleNamespace(responses=responses))
                with self.assertRaises(AIProviderUnavailable) as raised:
                    provider.analyze(self.context)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(responses.calls, [])


if __name__ == "__main__":
    unittest.main()
