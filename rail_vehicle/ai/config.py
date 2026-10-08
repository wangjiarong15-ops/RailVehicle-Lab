"""AI settings loaded from Streamlit secrets first, then environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


def _streamlit_secrets() -> Mapping[str, Any]:
    """Read secrets lazily so importing the app works without a secrets file."""
    try:
        import streamlit as st

        return st.secrets
    except Exception:
        # Streamlit raises when no secrets.toml exists; environment settings
        # remain available in that normal local-development case.
        return {}


def _lookup(secrets: Mapping[str, Any], environment: Mapping[str, str], key: str, default: Any) -> Any:
    if key in secrets:
        return secrets[key]
    return environment.get(key, default)


def _secret_api_key(secrets: Mapping[str, Any], environment: Mapping[str, str]) -> str | None:
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

    @classmethod
    def from_sources(
        cls,
        secrets: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "AIConfig":
        secret_values = _streamlit_secrets() if secrets is None else secrets
        env_values = os.environ if environ is None else environ
        try:
            timeout = float(_lookup(secret_values, env_values, "AI_TIMEOUT", 30))
            if not 1 <= timeout <= 300:
                timeout = 30.0
        except (TypeError, ValueError):
            timeout = 30.0
        return cls(
            provider=str(_lookup(secret_values, env_values, "AI_PROVIDER", "openai")).strip().lower(),
            model=str(_lookup(secret_values, env_values, "AI_MODEL", "")).strip(),
            enabled=_as_bool(_lookup(secret_values, env_values, "AI_ENABLED", "false")),
            timeout=timeout,
            api_key=_secret_api_key(secret_values, env_values),
        )
