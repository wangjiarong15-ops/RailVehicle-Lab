"""Configuration tests for optional Streamlit secrets and environment fallback."""

import unittest
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import streamlit
from streamlit.errors import StreamlitSecretNotFoundError

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.ui import ai_configuration_message, request_ai_analysis, status_message


def analysis_with_invalid_ai_config():
    import streamlit as st
    from unittest.mock import patch
    from rail_vehicle.pages import ai_center, run_analysis
    from tests.test_anomaly_review import samples
    vehicle = dict(id=1, vehicle_code="FICTIONAL", vehicle_type="虚构车型", vehicle_length_m=20,
                   mass_kg=40000, axle_count=4, bogie_count=2, max_speed_kmh=120, notes="")
    batches = [dict(id=1, file_name="fictional.csv", imported_at="2026-10-26", sample_count=5)]
    with patch.object(run_analysis, "list_vehicles", return_value=[vehicle]), \
         patch.object(run_analysis, "get_vehicle", return_value=vehicle), \
         patch.object(run_analysis, "list_vehicle_batches", return_value=batches), \
         patch.object(run_analysis, "list_batch_samples", return_value=samples()), \
         patch.object(ai_center.AIConfig, "from_sources", return_value=st.session_state["test_config"]), \
         patch.object(ai_center, "create_configured_provider", side_effect=AssertionError("AI must not be called")):
        if st.session_state.get("navigation_page") == "AI 智能分析中心":
            ai_center.render()
        else:
            run_analysis._render_dynamics_analysis()


class _MissingSecrets:
    """Mimic Streamlit's lazy secrets object when no file is configured."""

    def __iter__(self):
        raise StreamlitSecretNotFoundError("No secrets found")

    def __getitem__(self, key):
        raise StreamlitSecretNotFoundError("No secrets found")

    def __len__(self):
        raise StreamlitSecretNotFoundError("No secrets found")


class AIConfigTests(unittest.TestCase):
    def test_portable_valid_config_loads_without_exposing_key_in_repr(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RAIL_PORTABLE_HOME": directory}):
            path = Path(directory) / ".streamlit" / "secrets.toml"
            path.parent.mkdir()
            path.write_text('AI_ENABLED = true\nAI_PROVIDER = "deepseek"\nDEEPSEEK_API_KEY = "fictional-only-value"\n', encoding="utf-8")
            config = AIConfig.from_sources(environ={})
        self.assertTrue(config.enabled)
        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.api_key, "fictional-only-value")
        self.assertIsNone(config.configuration_error)
        self.assertIsNone(ai_configuration_message(config))
        self.assertNotIn("fictional-only-value", repr(config))

    def test_portable_invalid_config_reports_safe_code_and_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RAIL_PORTABLE_HOME": directory}):
            path = Path(directory) / ".streamlit" / "secrets.toml"
            path.parent.mkdir()
            path.write_text('DEEPSEEK_API_KEY = "fictional-only-value"\ninvalid = [', encoding="utf-8")
            config = AIConfig.from_sources(environ={"AI_ENABLED": "true"})
        self.assertEqual(config.configuration_error, "config_invalid")
        calls = []
        result = request_ai_analysis({}, config, has_data=True, provider_factory=lambda value: calls.append(value))
        self.assertEqual(result["code"], "config_invalid")
        self.assertIn("格式无效", status_message(result))
        self.assertNotIn("fictional-only-value", repr(result))
        self.assertEqual(calls, [])

    def test_portable_unreadable_config_has_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RAIL_PORTABLE_HOME": directory}), \
             patch.object(Path, "open", side_effect=PermissionError("private exception text")):
            config = AIConfig.from_sources(environ={})
        self.assertEqual(config.configuration_error, "config_unreadable")
        self.assertIn("无法读取", ai_configuration_message(config))
        self.assertNotIn("private exception text", ai_configuration_message(config))

    def test_portable_missing_config_remains_optional(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"RAIL_PORTABLE_HOME": directory}):
            config = AIConfig.from_sources(environ={})
        self.assertIsNone(config.configuration_error)
        self.assertFalse(config.enabled)
        self.assertIn("未启用", ai_configuration_message(config))

    def test_invalid_portable_config_keeps_analysis_and_pdf_available(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_function(analysis_with_invalid_ai_config, default_timeout=15)
        app.session_state["test_config"] = AIConfig(configuration_error="config_invalid")
        app.run()
        self.assertFalse(app.exception)
        self.assertGreaterEqual(len(app.get("plotly_chart")), 3)
        app.button(key="generate_analysis_report").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.session_state["analysis_report_pdf"].startswith(b"%PDF"))
        app.button(key="ai_center_link_single").click().run()
        self.assertTrue(any("格式无效" in item.value for item in app.warning))
        self.assertTrue(app.button(key="ai_center_generate").disabled)

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
