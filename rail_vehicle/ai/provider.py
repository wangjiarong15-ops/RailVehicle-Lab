"""Provider-neutral analysis service and deterministic local fake provider."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Protocol

from rail_vehicle.ai.schemas import (
    AIContractError,
    AIOutputParseError,
    validate_input_payload,
    validate_output,
)


class Provider(Protocol):
    """Implementations return raw JSON or a JSON object; service validates it."""

    def analyze(self, payload: dict[str, Any]) -> str | dict[str, Any]: ...


class AIProviderUnavailable(RuntimeError):
    """Safe, user-displayable reason the optional AI provider is unavailable."""

    def __init__(self, code: str, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.user_message = message
        self.diagnostics = diagnostics


def safe_provider_error(exc: Exception) -> AIProviderUnavailable:
    """Map SDK/network errors to fixed user messages without exposing details."""
    name = type(exc).__name__
    if name in {"APITimeoutError", "TimeoutError"}:
        return AIProviderUnavailable("timeout", "AI 请求超时，请稍后重试。")
    if name in {"APIConnectionError", "ConnectionError", "ConnectError"}:
        return AIProviderUnavailable("network", "无法连接 AI 服务，请检查网络后重试。")
    if name == "AuthenticationError":
        return AIProviderUnavailable("authentication", "AI 服务认证失败，请检查本地密钥配置。")
    if name == "RateLimitError":
        return AIProviderUnavailable("rate_limit", "AI 服务当前限流，请稍后重试。")
    if name in {"BadRequestError", "NotFoundError", "UnprocessableEntityError"}:
        return AIProviderUnavailable("request_rejected", "AI 服务拒绝了请求，请检查模型配置。")
    return AIProviderUnavailable("service_error", "AI 服务暂时不可用，现有本地分析结果不受影响。")


def input_fingerprint(payload: dict[str, Any]) -> str:
    validate_input_payload(payload)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_result_current(result: dict[str, Any], current_payload: dict[str, Any]) -> bool:
    """Whether a stored ephemeral result matches the current analysis inputs."""
    try:
        return result.get("input_fingerprint") == input_fingerprint(current_payload)
    except (TypeError, ValueError):
        return False


class AIAnalysisService:
    """The sole boundary that exposes only schema- and evidence-validated output."""

    def __init__(self, provider: Provider):
        self._provider = provider

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_input_payload(payload)
        try:
            raw_result = self._provider.analyze(deepcopy(payload))
            validated = validate_output(raw_result, payload)
        except AIProviderUnavailable as exc:
            result = {"status": "unavailable", "code": exc.code, "message": exc.user_message}
            if exc.diagnostics is not None:
                result["diagnostics"] = dict(exc.diagnostics)
            return result
        except AIOutputParseError:
            return {
                "status": "unavailable",
                "code": "response_parse_error",
                "message": "AI 返回文本不是有效 JSON，未展示结果。",
            }
        except AIContractError:
            return {
                "status": "unavailable",
                "code": "invalid_model_output",
                "message": "AI 返回内容未通过结构与证据校验，已阻止展示。",
            }
        return {
            "status": "available",
            "analysis": validated,
            "input_fingerprint": input_fingerprint(payload),
        }


class FakeProvider:
    """A test-only provider returning a preset response; it performs no I/O."""

    def __init__(self, response: str | dict[str, Any]):
        self.response = response
        self.calls = 0
        self.last_payload: dict[str, Any] | None = None

    def analyze(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        self.calls += 1
        self.last_payload = deepcopy(payload)
        return deepcopy(self.response)
