"""Strict current-result AI appendices; Fake Provider only, never paid requests."""

from copy import deepcopy
import importlib.util
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.experiment_context import build_ml_experiment_payload, build_vibration_experiment_payload
from rail_vehicle.ai.provider import AIAnalysisService, FakeProvider
from rail_vehicle.ai.schemas import collect_evidence_metadata
from rail_vehicle.ai.ui import selection_fingerprint
from rail_vehicle.demo_data import ml_demo_samples, vibration_demo_samples
from rail_vehicle.experiment_report import (
    build_ml_report, build_vibration_report, generate_experiment_pdf, validated_experiment_ai,
)
from rail_vehicle.ml_anomaly import detect_ml_anomalies
from rail_vehicle.pages import experiment_ai, experiment_export, ml_detection, vibration_analysis
from rail_vehicle.vibration import analyze_vibration
import rail_vehicle.experiment_report as reports


HAS_DEPS = bool(importlib.util.find_spec("sklearn") and importlib.util.find_spec("scipy"))
FINGERPRINT = "a"*64
SELECTION = dict(source="demo")
READY = AIConfig(enabled=True, provider="deepseek", model="offline-test-model", api_key="test-only-placeholder")


def valid_output(payload):
    item = next(i for i in collect_evidence_metadata(payload) if i["evidence_id"] in
                ("batch.B2.ml.ml_count", "experiment.vibration.lateral.dynamic_rms"))
    claim = dict(text="离线 Mock：请结合工况核对当前聚合指标。", evidence_ids=[item["evidence_id"]],
                 claim_scope={k: item[k] for k in ("category", "axis", "batch_refs")})
    return dict(summary=deepcopy(claim), observations=[deepcopy(claim)],
                possible_causes=[dict(deepcopy(claim), qualification="hypothesis")],
                further_checks=[dict(action="核对测量条件。", reason="未提供完整测量链路。",
                                     evidence_ids=claim["evidence_ids"], claim_scope=claim["claim_scope"])],
                limitations=[deepcopy(claim)])


def cached_result(payload):
    result = AIAnalysisService(FakeProvider(valid_output(payload))).analyze(payload)
    result["selection_fingerprint"] = selection_fingerprint(dict(selection=SELECTION, analysis_fingerprint=FINGERPRINT))
    result["ai_metadata"] = dict(provider="deepseek", model="offline-test-model", generated_at="2026-10-26T08:00:00+00:00")
    return result


def ml_app():
    from rail_vehicle.pages.ml_detection import render
    render()


def vibration_app():
    from rail_vehicle.pages.vibration_analysis import render
    render()


@unittest.skipUnless(HAS_DEPS, "Install optional .[ml,signal] dependencies")
class ExperimentPDFBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ml = detect_ml_anomalies(*ml_demo_samples())
        cls.vibration = analyze_vibration(vibration_demo_samples())

    def contexts(self):
        return [("ml", self.ml, build_ml_experiment_payload(self.ml), build_ml_report),
                ("vibration", self.vibration, build_vibration_experiment_payload(self.vibration), build_vibration_report)]

    def test_both_report_types_accept_current_fully_validated_ai(self):
        for kind, computed, payload, builder in self.contexts():
            with self.subTest(kind=kind):
                report = builder(computed, FINGERPRINT, SELECTION, ai_context=payload, ai_result=cached_result(payload))
                section = report["ai"]
                self.assertEqual(section["scope_validation"], "verified")
                self.assertEqual(section["provider"], "deepseek")
                self.assertEqual(section["model"], "offline-test-model")
                self.assertEqual(len(section["evidence"]), 1)
                self.assertEqual(section["evidence"][0]["evidence_id"], section["analysis"]["summary"]["evidence_ids"][0])
                self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))

    def test_missing_unavailable_and_failed_ai_preserve_deterministic_pdf(self):
        for kind, computed, payload, builder in self.contexts():
            for result in (None, dict(status="unavailable"), dict(status="validation_failed")):
                with self.subTest(kind=kind, status=result):
                    report = builder(computed, FINGERPRINT, SELECTION, ai_context=payload, ai_result=result)
                    self.assertIsNone(report["ai"])
                    self.assertGreater(len(report["facts"]), 0)
                    self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))

    def test_context_selection_and_full_analysis_fingerprints_must_match(self):
        for kind, computed, payload, builder in self.contexts():
            result = cached_result(payload)
            bad = deepcopy(result)
            bad["input_fingerprint"] = "b"*64
            self.assertIsNone(validated_experiment_ai(payload, bad, FINGERPRINT, SELECTION, kind))
            self.assertIsNone(validated_experiment_ai(payload, result, "b"*64, SELECTION, kind))
            self.assertIsNone(validated_experiment_ai(payload, result, FINGERPRINT, dict(source="history", vehicle=9), kind))

    def test_analysis_types_cannot_be_mixed(self):
        ml_payload = build_ml_experiment_payload(self.ml)
        vibration_payload = build_vibration_experiment_payload(self.vibration)
        self.assertIsNone(validated_experiment_ai(ml_payload, cached_result(ml_payload), FINGERPRINT, SELECTION, "vibration"))
        self.assertIsNone(validated_experiment_ai(vibration_payload, cached_result(vibration_payload), FINGERPRINT, SELECTION, "ml"))

    def test_missing_null_and_legacy_scope_are_rejected_even_in_cached_results(self):
        payload = build_ml_experiment_payload(self.ml)
        for marker in (None, "legacy_unverified", "unknown"):
            result = cached_result(payload)
            result["scope_validation"] = marker
            self.assertIsNone(validated_experiment_ai(payload, result, FINGERPRINT, SELECTION, "ml"))
        for null in (True, False):
            result = cached_result(payload)
            if null:
                result["analysis"]["summary"]["claim_scope"] = None
            else:
                result["analysis"]["summary"].pop("claim_scope")
            self.assertIsNone(validated_experiment_ai(payload, result, FINGERPRINT, SELECTION, "ml"))

    def test_evidence_and_scope_are_revalidated_at_pdf_boundary(self):
        payload = build_vibration_experiment_payload(self.vibration)
        for change in (lambda c: c.update(evidence_ids=["UNKNOWN"]),
                       lambda c: c["claim_scope"].update(category="event"),
                       lambda c: c["claim_scope"].update(axis="vertical"),
                       lambda c: c["claim_scope"].update(batch_refs=["B2"]),
                       lambda c: c.update(text="确认车辆故障")):
            result = cached_result(payload)
            change(result["analysis"]["summary"])
            report = build_vibration_report(self.vibration, FINGERPRINT, SELECTION, ai_context=payload, ai_result=result)
            self.assertIsNone(report["ai"])

    def test_metadata_allowlist_and_snapshot_do_not_forward_sensitive_fields(self):
        payload = build_ml_experiment_payload(self.ml)
        result = cached_result(payload)
        result["api_key"] = "PRIVATE_CREDENTIAL_SENTINEL"
        result["ai_metadata"]["api_key"] = "PRIVATE_CREDENTIAL_SENTINEL"
        report = build_ml_report(self.ml, FINGERPRINT, SELECTION, ai_context=payload, ai_result=result)
        self.assertNotIn("PRIVATE_CREDENTIAL_SENTINEL", str(report))
        result["analysis"]["summary"]["text"] = "MUTATED_AFTER_ASSEMBLY"
        self.assertNotIn("MUTATED_AFTER_ASSEMBLY", str(report))

    def test_long_chinese_claims_generate_actual_multipage_pdf(self):
        payload = build_ml_experiment_payload(self.ml)
        result = cached_result(payload)
        result["analysis"]["summary"]["text"] = "仅为离线分页测试，需结合测量条件复核。"*350
        report = build_ml_report(self.ml, FINGERPRINT, SELECTION, ai_context=payload, ai_result=result)
        self.assertIsNotNone(report["ai"])
        original = reports._append_experiment_ai
        with patch.object(reports, "_append_experiment_ai", wraps=original) as append:
            content = generate_experiment_pdf(report)
        self.assertTrue(content.startswith(b"%PDF-"))
        append.assert_called_once()  # no silent deterministic fallback

    def test_ai_layout_failure_falls_back_without_mutating_report_or_leaking_error(self):
        payload = build_ml_experiment_payload(self.ml)
        report = build_ml_report(self.ml, FINGERPRINT, SELECTION, ai_context=payload, ai_result=cached_result(payload))
        original = deepcopy(report)
        append = reports._append_experiment_ai
        def fail_ai(story, section, *args):
            if section is not None:
                raise ValueError("PRIVATE_LAYOUT_DETAIL")
            return append(story, section, *args)
        with patch.object(reports, "_append_experiment_ai", side_effect=fail_ai) as calls:
            content = generate_experiment_pdf(report)
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(report, original)


@unittest.skipUnless(HAS_DEPS, "Install optional .[ml,signal] dependencies")
class ExperimentAIPDFPageTests(unittest.TestCase):
    def demo(self, kind):
        page, fn, source, label, calculate = (
            (ml_detection, ml_app, "ml_source", "内置虚构实验", "ml_train") if kind == "ml" else
            (vibration_analysis, vibration_app, "vibration_source", "内置高频虚构信号", "vibration_calculate"))
        with patch.object(page, "list_vehicles", return_value=[]):
            app = AppTest.from_function(fn).run()
            app.radio(key=source).set_value(label).run()
            app.button(key=calculate).click().run()
        return app

    def test_new_ai_success_and_failure_both_invalidate_old_pdf_without_extra_requests(self):
        class Provider:
            calls = 0
            def analyze(self, payload):
                self.calls += 1
                result = valid_output(payload)
                if self.calls == 2:
                    result["summary"]["evidence_ids"] = ["UNKNOWN"]
                return result
        provider = Provider()
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=provider), \
             patch.object(experiment_export, "generate_experiment_pdf", wraps=generate_experiment_pdf) as generate:
            app = self.demo("ml")
            app.button(key="ml_report_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 1)
            self.assertIsNone(generate.call_args.args[0]["ai"])
            self.assertEqual(provider.calls, 0)
            app.button(key="ml_experiment_ai_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 0)
            app.button(key="ml_report_generate").click().run()
            self.assertIsNotNone(generate.call_args.args[0]["ai"])
            self.assertEqual(provider.calls, 1)
            app.button(key="ml_experiment_ai_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 0)
            app.button(key="ml_report_generate").click().run()
            self.assertIsNone(generate.call_args.args[0]["ai"])
            self.assertEqual(provider.calls, 2)
            self.assertEqual(len(app.exception), 0)

    def test_vibration_pdf_and_ai_removed_after_range_change(self):
        class Provider:
            def analyze(self, payload):
                return valid_output(payload)
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=Provider()), \
             patch.object(experiment_export, "generate_experiment_pdf", wraps=generate_experiment_pdf) as generate:
            app = self.demo("vibration")
            app.button(key="vibration_experiment_ai_generate").click().run()
            app.button(key="vibration_report_generate").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertIsNotNone(generate.call_args.args[0]["ai"])
            app.slider[0].set_value((0.0, 9.0)).run()
            self.assertEqual(len(app.get("download_button")), 0)
            self.assertNotIn("experiment_vibration_ai", app.session_state)
