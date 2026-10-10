"""Configuration tests for optional Streamlit secrets and environment fallback."""

import unittest
from unittest.mock import patch

import streamlit
from streamlit.errors import StreamlitSecretNotFoundError

from rail_vehicle.ai.config import AIConfig


class _MissingSecrets:
    """Mimic Streamlit's lazy secrets object when no file is configured."""

    def __iter__(self):
        raise StreamlitSecretNotFoundError("No secrets found")

    def __getitem__(self, key):
        raise StreamlitSecretNotFoundError("No secrets found")

    def __len__(self):
        raise StreamlitSecretNotFoundError("No secrets found")


class AIConfigTests(unittest.TestCase):
    def test_missing_streamlit_secrets_falls_back_to_environment(self):
        environment = {
            "AI_PROVIDER": "deepseek",
            "AI_MODEL": "deepseek-flash",
            "AI_ENABLED": "true",
            "DEEPSEEK_API_KEY": "unit-test-placeholder",
        }
        with patch.object(streamlit, "secrets", _MissingSecrets()):
            config = AIConfig.from_sources(environ=environment)

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.model, "deepseek-flash")
        self.assertTrue(config.enabled)
        self.assertEqual(config.api_key, "unit-test-placeholder")

    def test_missing_streamlit_secrets_without_environment_keeps_ai_optional(self):
        with patch.object(streamlit, "secrets", _MissingSecrets()):
            config = AIConfig.from_sources(environ={})

        self.assertFalse(config.enabled)
        self.assertIsNone(config.api_key)


if __name__ == "__main__":
    unittest.main()
