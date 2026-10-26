"""Experiment AI integration: deterministic contexts, mocked providers, no APIs."""

import importlib.util
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.deepseek_provider import DeepSeekResponsesProvider
from rail_vehicle.ai.experiment_context import build_ml_experiment_payload, build_vibration_experiment_payload
from rail_vehicle.ai.openai_provider import OpenAIResponsesProvider
from rail_vehicle.ai.prompts import build_evidence_reference_instructions
from rail_vehicle.ai.provider import AIAnalysisService, FakeProvider
from rail_vehicle.ai.schemas import AIContractError, collect_evidence_metadata, validate_input_payload, validate_output
from rail_vehicle.demo_data import ml_demo_samples, vibration_demo_samples
from rail_vehicle.ml_anomaly import detect_ml_anomalies
from rail_vehicle.pages import experiment_ai, ml_detection, vibration_analysis
from rail_vehicle.vibration import analyze_vibration


READY = AIConfig(enabled=True, provider="deepseek", model="mock-model", api_key="test-only-placeholder")
HAS_DEPS = bool(importlib.util.find_spec("sklearn") and importlib.util.find_spec("scipy"))


def response_for(payload, evidence_id=None):
    item = next(m for m in collect_evidence_metadata(payload) if evidence_id is None or m["evidence_id"] == evidence_id)
    return dict(summary=dict(text="当前实验提供了聚合指标，需结合测量条件解释。", evidence_ids=[item["evidence_id"]],
                             claim_scope={k: item[k] for k in ("category", "axis", "batch_refs")}),
                observations=[], possible_causes=[], further_checks=[], limitations=[])


def ml_app():
    from rail_vehicle.pages.ml_detection import render
    render()


def vibration_app():
    from rail_vehicle.pages.vibration_analysis import render
    render()


@unittest.skipUnless(HAS_DEPS, "Install optional .[ml,signal] dependencies")
class ExperimentContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ml = detect_ml_anomalies(*ml_demo_samples())
        cls.vibration = analyze_vibration(vibration_demo_samples())

    def test_ml_context_has_independent_reference_and_target_scopes(self):
        payload = build_ml_experiment_payload(self.ml)
        self.assertEqual(payload["analysis_type"], "ml_experiment")
        catalog = {e["evidence_id"]: e for e in collect_evidence_metadata(payload)}
        self.assertEqual(catalog["batch.B1.ml.reference_count"]["batch_refs"], ["B1"])
        self.assertEqual(catalog["batch.B2.ml.ml_count"]["batch_refs"], ["B2"])
        self.assertEqual(catalog["batch.B2.ml.ml_count"]["category"], "batch_metric")
        self.assertEqual(catalog["experiment.ml.score_threshold"]["batch_refs"], [])

    def test_builders_copy_facts_without_recalculating_or_sending_raw_arrays(self):
        changed = dict(self.ml, ml_count=123, filename="PRIVATE_FILE", notes="PRIVATE_NOTE", vehicle_id="PRIVATE_ID")
        payload = build_ml_experiment_payload(changed)
        facts = {m["evidence_id"]: m["value"] for b in payload["batches"] for m in b["metrics"]}
        self.assertEqual(facts["batch.B2.ml.ml_count"], 123)
        text = json.dumps(payload)
        for forbidden in ("PRIVATE_", '"records"', '"ml_score"', '"features"'):
            self.assertNotIn(forbidden, text)
        vibration = build_vibration_experiment_payload(self.vibration)
        text = json.dumps(vibration)
        for forbidden in ('"values"', '"psd"', '"rolling_rms"', '"offsets"', '"timestamps_utc"'):
            self.assertNotIn(forbidden, text)

    def test_vibration_axes_values_and_scope_match_computed_result(self):
        payload = build_vibration_experiment_payload(self.vibration)
        catalog = {e["evidence_id"]: e for e in collect_evidence_metadata(payload)}
        facts = {e["evidence_id"]: e["value"] for e in payload["metrics"]}
        for axis in ("lateral", "vertical"):
            eid = f"experiment.vibration.{axis}.dynamic_rms"
            self.assertEqual(catalog[eid]["axis"], axis)
            self.assertEqual(catalog[eid]["batch_refs"], ["B1"])
            self.assertEqual(facts[eid], self.vibration["channels"][axis+"_accel"]["dynamic_rms"])
            self.assertEqual(validate_output(response_for(payload, eid), payload)["summary"]["evidence_ids"], [eid])

    def test_low_rate_and_undefined_values_never_create_spectral_evidence(self):
        result = analyze_vibration([dict(timestamp="2026-01-01T00:00:00Z", speed_kmh=0, lateral_accel=0, vertical_accel=0)])
        payload = build_vibration_experiment_payload(result)
        ids = [m["evidence_id"] for m in payload["metrics"]]
        self.assertFalse(any("dominant_frequency" in eid or "band_rms" in eid or "crest_factor" in eid for eid in ids))
        self.assertIn("频域分析停用", " ".join(payload["experiment"]["limitations"]))

    def test_invalid_metadata_and_non_finite_input_rejected(self):
        payload = build_ml_experiment_payload(self.ml)
        del payload["experiment"]
        with self.assertRaises(AIContractError):
            validate_input_payload(payload)
        with self.assertRaises(AIContractError):
            build_ml_experiment_payload(dict(self.ml, score_threshold=float("nan")))

    def test_scope_unknown_id_category_axis_batch_and_missing_scope_still_rejected(self):
        contexts = [(build_ml_experiment_payload(self.ml), "batch.B2.ml.ml_count"),
                    (build_vibration_experiment_payload(self.vibration), "experiment.vibration.lateral.dynamic_rms")]
        for payload, eid in contexts:
            cases = [
                ("evidence_id_unknown", lambda c: c.update(evidence_ids=["UNKNOWN"])),
                ("evidence_scope_category_mismatch", lambda c: c["claim_scope"].update(category="event")),
                ("evidence_scope_axis_mismatch", lambda c: c["claim_scope"].update(axis="vertical")),
                ("evidence_scope_batch_mismatch", lambda c: c["claim_scope"].update(batch_refs=["B-OTHER"])),
                ("claim_scope_schema_invalid", lambda c: c.pop("claim_scope")),
                ("claim_scope_schema_invalid", lambda c: c.update(claim_scope=None)),
                ("unsafe_claim_text", lambda c: c.update(text="确认车辆故障")),
            ]
            for code, mutate in cases:
                output = response_for(payload, eid)
                mutate(output["summary"])
                with self.subTest(type=payload["analysis_type"], code=code), self.assertRaises(AIContractError) as error:
                    validate_output(output, payload)
                self.assertEqual(error.exception.code, code)

    def test_both_providers_send_shared_experiment_guidance_and_pass_validation(self):
        for payload in (build_ml_experiment_payload(self.ml), build_vibration_experiment_payload(self.vibration)):
            for provider_type, provider_name in ((DeepSeekResponsesProvider, "deepseek"), (OpenAIResponsesProvider, "openai")):
                responses = Mock()
                responses.create.return_value = SimpleNamespace(status="completed", output_text=json.dumps(response_for(payload)))
                provider = provider_type(AIConfig(enabled=True, provider=provider_name, model="mock-model", api_key="test-only-placeholder"),
                                         client=SimpleNamespace(responses=responses))
                result = AIAnalysisService(provider).analyze(payload)
                self.assertEqual(result["status"], "available")
                self.assertEqual(result["scope_validation"], "verified")
                request = responses.create.call_args.kwargs
                prompt = "".join(c["text"] for m in request["input"] for c in m["content"])
                self.assertIn(build_evidence_reference_instructions(payload), prompt)
                self.assertIn("不得自行计算比例", prompt)
                self.assertIn("不是故障概率", prompt)
                self.assertIn(payload["analysis_type"], prompt)
                responses.create.assert_called_once()


@unittest.skipUnless(HAS_DEPS, "Install optional .[ml,signal] dependencies")
class ExperimentAIPageTests(unittest.TestCase):
    def demo(self, kind):
        page, fn, source, value = (ml_detection, ml_app, "ml_source", "内置虚构实验") if kind == "ml" else (
            vibration_analysis, vibration_app, "vibration_source", "内置高频虚构信号")
        with patch.object(page, "list_vehicles", return_value=[]):
            app = AppTest.from_function(fn).run()
            app.radio(key=source).set_value(value).run()
            app.button(key="ml_train" if kind == "ml" else "vibration_calculate").click().run()
        return app

    def test_disabled_ai_has_no_provider_calls_and_pdf_remains_available(self):
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=AIConfig(enabled=False)), \
             patch.object(experiment_ai, "create_configured_provider") as factory:
            app = self.demo("ml")
            self.assertTrue(app.button(key="ml_experiment_ai_generate").disabled)
            app.button(key="ml_report_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 1)
            factory.assert_not_called()

    def test_missing_key_and_no_calculation_cannot_trigger_ai(self):
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=AIConfig(enabled=True, provider="deepseek")), \
             patch.object(experiment_ai, "create_configured_provider") as factory:
            with patch.object(ml_detection, "list_vehicles", return_value=[]):
                app = AppTest.from_function(ml_app).run()
                self.assertEqual(len(app.button), 0)
            app = self.demo("ml")
            self.assertTrue(app.button(key="ml_experiment_ai_generate").disabled)
            self.assertTrue(any("未配置" in item.value for item in app.warning))
            factory.assert_not_called()

    def test_ml_success_is_manual_and_parameters_clear_old_result(self):
        captured = []
        def factory(config):
            class Provider:
                def analyze(self, payload):
                    captured.append(payload)
                    return response_for(payload, "batch.B2.ml.ml_count")
            return Provider()
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", side_effect=factory) as dispatch:
            app = self.demo("ml")
            dispatch.assert_not_called()
            app.button(key="ml_experiment_ai_generate").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(captured[0]["analysis_type"], "ml_experiment")
            self.assertTrue(any("batch.B2.ml.ml_count" in item.value for item in app.caption))
            self.assertEqual(app.session_state["experiment_ml_ai"]["scope_validation"], "verified")
            app.number_input(key="ml_rule_threshold").set_value(2.0).run()
            self.assertNotIn("experiment_ml_ai", app.session_state)
            self.assertFalse(any("当前实验提供了聚合指标" in item.value for item in app.text))
            dispatch.assert_called_once()

    def test_vibration_success_uses_correct_context_and_range_invalidation(self):
        seen = []
        class Provider:
            def analyze(self, payload):
                seen.append(payload)
                return response_for(payload, "experiment.vibration.vertical.raw_rms")
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=Provider()):
            app = self.demo("vibration")
            app.button(key="vibration_experiment_ai_generate").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(seen[0]["analysis_type"], "vibration_experiment")
            self.assertIn("experiment_vibration_ai", app.session_state)
            app.slider[0].set_value((0.0, 9.0)).run()
            self.assertNotIn("experiment_vibration_ai", app.session_state)
            self.assertEqual(len(seen), 1)

    def test_rejected_model_text_is_hidden_and_does_not_break_pdf(self):
        bad = dict(summary=dict(text="PRIVATE_MODEL_BODY", evidence_ids=["UNKNOWN"],
                                claim_scope=dict(category="metric", axis="not_applicable", batch_refs=[])),
                   observations=[], possible_causes=[], further_checks=[], limitations=[])
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=FakeProvider(bad)):
            app = self.demo("ml")
            app.button(key="ml_experiment_ai_generate").click().run()
            self.assertNotIn("experiment_ml_ai", app.session_state)
            self.assertTrue(any("未知证据" in w.value for w in app.warning))
            self.assertNotIn("PRIVATE_MODEL_BODY", " ".join(t.value for t in app.text))
            app.button(key="ml_report_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 1)

    def test_provider_exception_is_safe_and_experiment_stays_visible(self):
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", side_effect=RuntimeError("PRIVATE_NETWORK_DETAIL")):
            app = self.demo("ml")
            app.button(key="ml_experiment_ai_generate").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 4)
            self.assertNotIn("PRIVATE_NETWORK_DETAIL", " ".join(w.value for w in app.warning))

    def test_configuration_failure_does_not_block_analysis_or_pdf(self):
        with patch.object(experiment_ai.AIConfig, "from_sources", side_effect=RuntimeError("PRIVATE_CONFIG_DETAIL")):
            app = self.demo("ml")
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 4)
            app.button(key="ml_report_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 1)
            self.assertNotIn("PRIVATE_CONFIG_DETAIL", " ".join(w.value for w in app.warning))

    def test_legacy_experiment_cache_is_not_treated_as_verified(self):
        def cached_app():
            import streamlit as st
            from rail_vehicle.ai.experiment_context import build_ml_experiment_payload
            from rail_vehicle.ai.provider import input_fingerprint
            from rail_vehicle.ai.ui import selection_fingerprint
            from rail_vehicle.demo_data import ml_demo_samples
            from rail_vehicle.ml_anomaly import detect_ml_anomalies
            from rail_vehicle.pages.experiment_ai import render_experiment_ai
            result = detect_ml_anomalies(*ml_demo_samples())
            payload = build_ml_experiment_payload(result)
            st.session_state["experiment_ml_ai"] = dict(
                status="available", scope_validation="legacy_unverified",
                input_fingerprint=input_fingerprint(payload),
                selection_fingerprint=selection_fingerprint(dict(selection=dict(source="demo"), analysis_fingerprint="a"*64)),
                analysis=dict(summary=dict(text="PRIVATE_LEGACY_TEXT")))
            render_experiment_ai(dict(fingerprint="a"*64, result=result), "a"*64, dict(source="demo"), "ml")
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY):
            app = AppTest.from_function(cached_app).run()
        self.assertEqual(len(app.exception), 0)
        self.assertNotIn("experiment_ml_ai", app.session_state)
        self.assertTrue(any("未完整验证" in w.value for w in app.warning))
        self.assertNotIn("PRIVATE_LEGACY_TEXT", " ".join(t.value for t in app.text))
