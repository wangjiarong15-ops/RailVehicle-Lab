"""AI settings loaded from Streamlit secrets first, then environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


def _streamlit_secrets() -> Mapping[str, Any]:
    """Materialize Streamlit secrets safely when no secrets file is configured."""
    try:
        import streamlit as st

        # Streamlit loads secrets lazily. Returning ``st.secrets`` directly
        # defers its missing-file exception until a later ``get`` call, outside
        # this guard, and prevents environment-variable fallback.
        return dict(st.secrets)
    except Exception:
        # Streamlit raises when no secrets.toml exists; environment settings
        # remain available in that normal local-development case.
        return {}


def _lookup(secrets: Mapping[str, Any], environment: Mapping[str, str], key: str, default: Any) -> Any:
    if key in secrets:
        return secrets[key]
    return environment.get(key, default)


def _secret_api_key(
    provider: str, secrets: Mapping[str, Any], environment: Mapping[str, str]
) -> str | None:
    if provider == "deepseek":
        value = secrets.get("DEEPSEEK_API_KEY")
        if value is None:
            deepseek_section = secrets.get("deepseek", {})
            ai_section = secrets.get("ai", {})
            if isinstance(deepseek_section, Mapping):
                value = deepseek_section.get("api_key")
            if value is None and isinstance(ai_section, Mapping):
                value = ai_section.get("deepseek_api_key")
        if value is None:
            value = environment.get("DEEPSEEK_API_KEY")
    else:
        value = secrets.get("OPENAI_API_KEY")
        if value is None:
            ai_section = secrets.get("ai", {})
            if isinstance(ai_section, Mapping):
                value = ai_section.get("api_key")
        if value is None:
            value = environment.get("OPENAI_API_KEY")
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AIConfig:
    provider: str = "openai"
    model: str = ""
    enabled: bool = False
    timeout: float = 30.0
    api_key: str | None = None
    base_url: str | None = None

    @classmethod
    def from_sources(
        cls,
        secrets: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "AIConfig":
        secret_values = _streamlit_secrets() if secrets is None else secrets
        env_values = os.environ if environ is None else environ
        provider = str(_lookup(secret_values, env_values, "AI_PROVIDER", "openai")).strip().lower()
        try:
            timeout = float(_lookup(secret_values, env_values, "AI_TIMEOUT", 30))
            if not 1 <= timeout <= 300:
                timeout = 30.0
        except (TypeError, ValueError):
            timeout = 30.0
        deepseek_base_url = None
        if provider == "deepseek":
            deepseek_secrets = secret_values.get("deepseek", {})
            nested_base_url = (
                deepseek_secrets.get("base_url")
                if isinstance(deepseek_secrets, Mapping)
                else None
            )
            configured_base_url = _lookup(
                secret_values,
                env_values,
                "DEEPSEEK_BASE_URL",
                nested_base_url or DEEPSEEK_DEFAULT_BASE_URL,
            )
            deepseek_base_url = str(configured_base_url).strip() or DEEPSEEK_DEFAULT_BASE_URL

        return cls(
            provider=provider,
            model=str(
                _lookup(
                    secret_values,
                    env_values,
                    "AI_MODEL",
                    "deepseek-flash" if provider == "deepseek" else "",
                )
            ).strip(),
            enabled=_as_bool(_lookup(secret_values, env_values, "AI_ENABLED", "false")),
            timeout=timeout,
            api_key=_secret_api_key(provider, secret_values, env_values),
            base_url=deepseek_base_url,
        )
