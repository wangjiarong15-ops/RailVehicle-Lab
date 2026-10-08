"""Provider-neutral analysis service and deterministic local fake provider."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Protocol

from rail_vehicle.ai.schemas import AIContractError, validate_input_payload, validate_output


class Provider(Protocol):
    """Implementations return raw JSON or a JSON object; service validates it."""

    def analyze(self, payload: dict[str, Any]) -> str | dict[str, Any]: ...


class AIProviderUnavailable(RuntimeError):
    """Safe, user-displayable reason the optional AI provider is unavailable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.user_message = message


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
            return {"status": "unavailable", "code": exc.code, "message": exc.user_message}
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
