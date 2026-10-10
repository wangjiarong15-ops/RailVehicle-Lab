"""Small, testable helpers for opt-in Streamlit AI interpretation sections."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.provider import (
    AIAnalysisService,
    AIProviderUnavailable,
    Provider,
    input_fingerprint,
)
from rail_vehicle.ai.schemas import AIContractError, validate_output


def selection_fingerprint(selection: Any) -> str:
    """Hash local UI selections; this hash is never included in the AI request."""
    encoded = json.dumps(selection, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def invalidate_stale_result(
    session_state: Any,
    state_key: str,
    payload: dict[str, Any],
    selection: Any,
) -> bool:
    """Drop cached output unless both sanitized context and UI selections match."""
    saved = session_state.get(state_key)
    if saved is None:
        return False
    still_matches = (
        saved.get("input_fingerprint") == input_fingerprint(payload)
        and saved.get("selection_fingerprint") == selection_fingerprint(selection)
    )
    if not still_matches:
        session_state.pop(state_key, None)
    return not still_matches


def clear_result(session_state: Any, state_key: str) -> None:
    session_state.pop(state_key, None)


def ai_configuration_message(config: AIConfig) -> str | None:
    if not config.enabled:
        return "AI 辅助解读当前未启用，请配置 AI_ENABLED 和 API Key。"
    if config.provider not in {"openai", "deepseek"}:
        return "当前 AI_PROVIDER 不受支持。"
    if not config.api_key:
        if config.provider == "deepseek":
            return "DeepSeek 服务未配置 DEEPSEEK_API_KEY。"
        return "OpenAI 服务未配置 OPENAI_API_KEY。"
    if not config.model:
        return "AI 服务未配置 AI_MODEL。"
    return None


def request_ai_analysis(
    payload: dict[str, Any],
    config: AIConfig,
    *,
    has_data: bool,
    provider_factory: Callable[[AIConfig], Provider],
) -> dict[str, Any]:
    """Call the abstraction only after explicit UI activation and readiness checks."""
    if not has_data:
        return {"status": "unavailable", "code": "no_data", "message": "当前分析没有数据，无法生成 AI 解读。"}
    message = ai_configuration_message(config)
    if message:
        code = "disabled" if not config.enabled else "missing_api_key" if not config.api_key else "not_configured"
        return {"status": "unavailable", "code": code, "message": message}
    try:
        result = AIAnalysisService(provider_factory(config)).analyze(payload)
        if result.get("code") == "invalid_model_output":
            result["status"] = "validation_failed"
        return result
    except AIProviderUnavailable as exc:
        return {"status": "unavailable", "code": exc.code, "message": exc.user_message}
    except Exception:
        return {"status": "unavailable", "code": "service_error", "message": "AI 服务暂时不可用，请稍后重试。"}


def status_message(result: dict[str, Any]) -> str:
    if result.get("status") == "validation_failed":
        return "AI 返回内容未通过结构与证据校验，已阻止展示。"
    messages = {
        "disabled": "AI 辅助解读当前未启用，请配置 AI_ENABLED 和 API Key。",
        "missing_api_key": "AI 服务未配置 API Key。",
        "not_configured": "AI 服务配置不完整，请检查 AI_PROVIDER 和 AI_MODEL。",
        "timeout": "AI 请求超时，请稍后重试。",
        "network": "AI 服务暂时不可用，请检查网络后重试。",
        "authentication": "AI 认证失败，请检查 API Key 配置。",
        "rate_limit": "AI 服务当前限流，请稍后重试。",
        "invalid_model_output": "AI 返回内容未通过结构与证据校验，已阻止展示。",
        "invalid_response": "AI 服务响应中没有可用的解读文本，未展示结果。",
        "response_parse_error": "AI 返回文本不是有效 JSON，未展示结果。",
        "incomplete_response": "AI 响应未完整生成，未展示结果，请稍后重试。",
        "response_failed": "AI 服务未能生成分析结果，请稍后重试。",
        "request_rejected": "AI 服务拒绝了请求，请检查模型配置。",
        "provider": "当前 AI_PROVIDER 不受支持。",
        "missing_model": "AI 服务未配置 AI_MODEL。",
        "service_error": "AI 服务暂时不可用，请稍后重试。",
        "no_data": "当前分析没有数据，无法生成 AI 解读。",
    }
    message = messages.get(result.get("code", ""), "AI 服务暂时不可用，请稍后重试。")
    diagnostics = result.get("diagnostics")
    if result.get("code") in {"incomplete_response", "response_failed", "invalid_response"} and isinstance(diagnostics, dict):
        allowed_statuses = {"completed", "incomplete", "in_progress", "failed", "unknown", "other"}
        allowed_reasons = {"max_output_tokens", "content_filter", "other"}
        status = diagnostics.get("status")
        reason = diagnostics.get("incomplete_reason")
        length = diagnostics.get("output_text_length")
        token_limit = diagnostics.get("max_output_tokens_limit")
        used_tokens = diagnostics.get("usage_output_tokens")
        if not isinstance(status, str) or status not in allowed_statuses:
            status = "other"
        if reason is not None and (
            not isinstance(reason, str) or reason not in allowed_reasons
        ):
            reason = "none" if reason is None else "other"
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            length = 0
        if not isinstance(token_limit, int) or isinstance(token_limit, bool) or token_limit < 0:
            token_limit = 0
        if not isinstance(used_tokens, int) or isinstance(used_tokens, bool) or used_tokens < 0:
            used_tokens = "unknown"

        message += (
            "\n安全诊断（不含请求或响应正文）："
            f"status={status}; incomplete_details.reason={reason}; "
            f"message_present={'yes' if diagnostics.get('has_message') is True else 'no'}; "
            f"output_text_present={'yes' if diagnostics.get('has_output_text') is True else 'no'}; "
            f"output_text_length={length}; "
            f"max_output_tokens_limit={token_limit}; usage_output_tokens={used_tokens}; "
            f"refusal_present={'yes' if diagnostics.get('has_refusal') is True else 'no'}"
        )
    return message


def render_validated_result(result: dict[str, Any], payload: dict[str, Any], st_module: Any) -> bool:
    """Render only after rechecking the output against this exact input context."""
    try:
        if result.get("input_fingerprint") != input_fingerprint(payload):
            raise AIContractError("AI 结果不属于当前分析输入。")
        analysis = validate_output(result["analysis"], payload)
    except (AttributeError, KeyError, TypeError, AIContractError):
        st_module.error("AI 结果校验失败，已阻止显示。")
        return False

    st_module.markdown("**核心结论**")
    st_module.text(analysis["summary"]["text"])
    _render_evidence(st_module, analysis["summary"]["evidence_ids"])

    st_module.markdown("**主要观察**")
    if analysis["observations"]:
        for index, observation in enumerate(analysis["observations"], start=1):
            st_module.text(f"{index}. {observation['text']}")
            _render_evidence(st_module, observation["evidence_ids"])
    else:
        st_module.caption("暂无主要观察。")

    st_module.markdown("**可能原因**")
    if analysis["possible_causes"]:
        for cause in analysis["possible_causes"]:
            st_module.text(f"{cause['text']}（待核实的假设：{cause['qualification']}）")
            _render_evidence(st_module, cause["evidence_ids"])
    else:
        st_module.caption("暂无可能原因假设。")

    st_module.markdown("**建议进一步检查**")
    if analysis["further_checks"]:
        for check in analysis["further_checks"]:
            st_module.text(f"建议：{check['action']}")
            st_module.text(f"依据：{check['reason']}")
            _render_evidence(st_module, check["evidence_ids"])
    else:
        st_module.caption("暂无进一步检查建议。")

    st_module.markdown("**局限性**")
    if analysis["limitations"]:
        for limitation in analysis["limitations"]:
            st_module.text(limitation["text"])
            _render_evidence(st_module, limitation["evidence_ids"])
    else:
        st_module.caption("暂无补充局限性说明。")
    return True


def _render_evidence(st_module: Any, evidence_ids: list[str]) -> None:
    st_module.caption("证据：" + " · ".join(evidence_ids))
