"""OpenAI Responses API adapter for the provider-neutral analysis service."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.provider import AIProviderUnavailable, Provider, safe_provider_error
from rail_vehicle.ai.prompts import build_evidence_reference_instructions
from rail_vehicle.ai.schemas import CLAIM_SCOPE_SCHEMA, validate_input_payload


MAX_OUTPUT_TOKENS = 1200
MAX_RETRIES = 1


def _claim_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "claim_scope": CLAIM_SCOPE_SCHEMA,
        },
        "required": ["text", "evidence_ids", "claim_scope"],
        "additionalProperties": False,
    }


def _further_check_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "reason": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "claim_scope": CLAIM_SCOPE_SCHEMA,
        },
        "required": ["action", "reason", "evidence_ids", "claim_scope"],
        "additionalProperties": False,
    }


def _analysis_output_schema(*, strict_compatible: bool = False) -> dict[str, Any]:
    """Return the shared JSON Schema sent to compatible model providers.

    The local validator remains authoritative for min/max items, constants,
    finite values, and evidence IDs. Every object lists all keys as required
    and forbids additional properties for Structured Outputs compatibility.
    """
    claim = _claim_schema()
    possible_cause = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "qualification": {"type": "string", "enum": ["hypothesis"]},
            "claim_scope": CLAIM_SCOPE_SCHEMA,
        },
        "required": ["text", "evidence_ids", "qualification", "claim_scope"],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "properties": {
            "summary": claim,
            "observations": {"type": "array", "items": claim},
            "possible_causes": {"type": "array", "items": possible_cause},
            "further_checks": {"type": "array", "items": _further_check_schema()},
            "limitations": {"type": "array", "items": claim},
        },
        "required": ["summary", "observations", "possible_causes", "further_checks", "limitations"],
        "additionalProperties": False,
    }
    if strict_compatible:
        # OpenAI strict Structured Outputs requires every property to be
        # required. claim_scope is now required and non-null for new output.
        schema = deepcopy(schema)
        claim_nodes = [
            schema["properties"]["summary"],
            schema["properties"]["observations"]["items"],
            schema["properties"]["possible_causes"]["items"],
            schema["properties"]["further_checks"]["items"],
            schema["properties"]["limitations"]["items"],
        ]
        for node in claim_nodes:
            node["properties"]["claim_scope"] = CLAIM_SCOPE_SCHEMA
            # The claim schema is shared by summary, observations, and
            # limitations. Deepcopy preserves that alias, so this loop may
            # visit the same object more than once.
            if "claim_scope" not in node["required"]:
                node["required"].append("claim_scope")
    return schema


class OpenAIResponsesProvider:
    """Calls OpenAI Responses with only a validated, preassembled context."""

    def __init__(
        self,
        config: AIConfig,
        client: Any | None = None,
        client_factory: Callable[[AIConfig], Any] | None = None,
    ):
        self._config = config
        self._client = client
        self._client_factory = client_factory or self._build_client

    @staticmethod
    def _build_client(config: AIConfig) -> Any:
        from openai import OpenAI

        return OpenAI(
            api_key=config.api_key,
            timeout=config.timeout,
            max_retries=MAX_RETRIES,
        )

    def analyze(self, payload: dict[str, Any]) -> str:
        validate_input_payload(payload)
        if not self._config.enabled:
            raise AIProviderUnavailable("disabled", "AI 分析未启用。")
        if self._config.provider != "openai":
            raise AIProviderUnavailable("provider", "当前 AI_PROVIDER 不受支持。")
        if not self._config.api_key:
            raise AIProviderUnavailable("missing_api_key", "未配置 AI API Key，AI 分析不可用。")
        if not self._config.model:
            raise AIProviderUnavailable("missing_model", "未配置 AI_MODEL，AI 分析不可用。")

        prompt = (
            "请基于给定的已计算指标生成简洁的轨道车辆运行统计分析。"
            "只描述输入支持的观测；所有结论至少引用一个 evidence_id。"
            "可能原因只能作为假设，不得判断车辆故障、安全事故或法规合规。"
            "不得重新计算指标；对于批次对比，不得自行计算差值或变化百分比，只解释输入已提供的批次结果。"
            "严格输出指定 JSON 结构。\n\n"
            + build_evidence_reference_instructions(payload)
            + "\n"
            + "已计算分析上下文：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        )
        try:
            client = self._client if self._client is not None else self._client_factory(self._config)
            response = client.responses.create(
                model=self._config.model,
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": "你是严谨的车辆运行数据分析助手。不得重算指标或引入未提供事实。"}],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "rail_vehicle_analysis",
                        "strict": True,
                        "schema": _analysis_output_schema(strict_compatible=True),
                    }
                },
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
            )
        except Exception as exc:
            raise safe_provider_error(exc) from None

        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise AIProviderUnavailable("invalid_response", "AI 返回了空结果或非结构化结果。")
        return output_text


def create_configured_provider(config: AIConfig | None = None) -> Provider:
    """Select a configured adapter without connecting or loading its SDK."""
    resolved = config or AIConfig.from_sources()
    if resolved.provider == "openai":
        return OpenAIResponsesProvider(resolved)
    if resolved.provider == "deepseek":
        from rail_vehicle.ai.deepseek_provider import DeepSeekResponsesProvider

        return DeepSeekResponsesProvider(resolved)
    raise AIProviderUnavailable("provider", "当前 AI_PROVIDER 不受支持。")
