"""DeepSeek Responses API adapter using its documented OpenAI SDK endpoint."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Callable

from rail_vehicle.ai.config import AIConfig, DEEPSEEK_DEFAULT_BASE_URL
from rail_vehicle.ai.openai_provider import _analysis_output_schema
from rail_vehicle.ai.provider import AIProviderUnavailable, safe_provider_error
from rail_vehicle.ai.schemas import validate_input_payload


DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
MAX_OUTPUT_TOKENS = 1200
MAX_RETRIES = 1
RESPONSE_STATUSES = {"completed", "incomplete", "in_progress", "failed"}
INCOMPLETE_REASONS = {"max_output_tokens", "content_filter"}


def _response_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _extract_output_text(response: Any) -> str:
    """Read only final assistant output text from the Responses API shape."""
    try:
        convenience_text = _response_value(response, "output_text")
    except Exception:
        convenience_text = None
    if isinstance(convenience_text, str) and convenience_text.strip():
        return convenience_text

    output = _response_value(response, "output", [])
    if not isinstance(output, (list, tuple)):
        return ""

    text_parts: list[str] = []
    for item in output:
        if _response_value(item, "type") != "message":
            continue
        content = _response_value(item, "content", [])
        if not isinstance(content, (list, tuple)):
            continue
        for part in content:
            if _response_value(part, "type") != "output_text":
                continue
            text = _response_value(part, "text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text)
    return "\n".join(text_parts)


def _response_diagnostics(response: Any, output_text: str) -> dict[str, Any]:
    """Return a small, content-free summary safe to show in the UI."""
    status = _response_value(response, "status")
    if not isinstance(status, str) or status not in RESPONSE_STATUSES:
        status = "unknown" if status is None else "other"

    details = _response_value(response, "incomplete_details")
    reason = _response_value(details, "reason") if details is not None else None
    if reason is not None and (
        not isinstance(reason, str) or reason not in INCOMPLETE_REASONS
    ):
        reason = "other"

    output = _response_value(response, "output", [])
    items = output if isinstance(output, (list, tuple)) else []
    usage = _response_value(response, "usage")
    usage_output_tokens = _response_value(usage, "output_tokens") if usage is not None else None
    if not isinstance(usage_output_tokens, int) or isinstance(usage_output_tokens, bool) or usage_output_tokens < 0:
        usage_output_tokens = None
    has_message = False
    has_output_text = False
    has_refusal = False
    for item in items:
        if _response_value(item, "type") != "message":
            continue
        has_message = True
        item_refusal = _response_value(item, "refusal")
        if isinstance(item_refusal, str) and item_refusal.strip():
            has_refusal = True
        content = _response_value(item, "content", [])
        parts = content if isinstance(content, (list, tuple)) else []
        for part in parts:
            if _response_value(part, "type") == "output_text":
                has_output_text = True
            if _response_value(part, "type") == "refusal":
                has_refusal = True
            refusal_text = _response_value(part, "refusal")
            if isinstance(refusal_text, str) and refusal_text.strip():
                has_refusal = True

    return {
        "status": status,
        "incomplete_reason": reason,
        "has_message": has_message,
        "has_output_text": has_output_text or bool(output_text.strip()),
        "output_text_length": len(output_text),
        "has_refusal": has_refusal,
        "max_output_tokens_limit": MAX_OUTPUT_TOKENS,
        "usage_output_tokens": usage_output_tokens,
    }


class DeepSeekResponsesProvider:
    """Calls DeepSeek's Responses endpoint and returns text for shared validation."""

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
            base_url=config.base_url or DEEPSEEK_DEFAULT_BASE_URL,
            timeout=config.timeout,
            max_retries=MAX_RETRIES,
        )

    def analyze(self, payload: dict[str, Any]) -> str:
        validate_input_payload(payload)
        if not self._config.enabled:
            raise AIProviderUnavailable("disabled", "AI 分析未启用。")
        if self._config.provider != "deepseek":
            raise AIProviderUnavailable("provider", "当前 AI_PROVIDER 与 DeepSeek Provider 不匹配。")
        if not self._config.api_key:
            raise AIProviderUnavailable("missing_api_key", "未配置 DeepSeek API Key，AI 分析不可用。")

        prompt = (
            "请基于给定的已计算指标生成简洁的轨道车辆运行统计分析。"
            "只描述输入支持的观测；所有结论至少引用一个 evidence_id。"
            "可能原因只能作为假设，不得判断车辆故障、安全事故或法规合规。"
            "不得重新计算指标；对于批次对比，不得自行计算差值或变化百分比，只解释输入已提供的批次结果。"
            "严格输出指定 JSON 结构。\n\n"
            "已计算分析上下文：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        )
        try:
            client = self._client if self._client is not None else self._client_factory(self._config)
            response = client.responses.create(
                model=self._config.model or DEFAULT_DEEPSEEK_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "你是严谨的车辆运行数据分析助手。不得重算指标或引入未提供事实。",
                            }
                        ],
                    },
                    {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "rail_vehicle_analysis",
                        "schema": _analysis_output_schema(),
                    }
                },
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except Exception as exc:
            raise safe_provider_error(exc) from None

        response_status = _response_value(response, "status")
        output_text = _extract_output_text(response)
        diagnostics = _response_diagnostics(response, output_text)
        response_status = diagnostics["status"]
        if response_status in {"incomplete", "in_progress"}:
            raise AIProviderUnavailable(
                "incomplete_response",
                "AI 响应未完整生成，未展示结果，请稍后重试。",
                diagnostics=diagnostics,
            )
        if response_status == "failed":
            raise AIProviderUnavailable(
                "response_failed",
                "AI 服务未能生成分析结果，请稍后重试。",
                diagnostics=diagnostics,
            )
        if response_status != "completed":
            raise AIProviderUnavailable(
                "invalid_response",
                "AI 服务返回了无法确认完成状态的响应，未展示结果。",
                diagnostics=diagnostics,
            )

        if not isinstance(output_text, str) or not output_text.strip():
            raise AIProviderUnavailable(
                "invalid_response",
                "AI 服务响应中没有可用的解读文本，未展示结果。",
                diagnostics=diagnostics,
            )
        return output_text
