"""Cross-vehicle AI aggregate privacy, strict scopes and manual UI lifecycle."""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.deepseek_provider import DeepSeekResponsesProvider
from rail_vehicle.ai.openai_provider import OpenAIResponsesProvider
from rail_vehicle.ai.fleet_context import build_fleet_comparison_payload
from rail_vehicle.ai.prompts import build_evidence_reference_instructions
from rail_vehicle.ai.provider import AIAnalysisService, FakeProvider
from rail_vehicle.ai.schemas import AIContractError, collect_evidence_metadata, validate_output
from rail_vehicle.fleet_comparison import compare_vehicles
from rail_vehicle.pages import fleet_comparison as page, experiment_ai


READY = AIConfig(enabled=True, provider="deepseek", model="mock-model", api_key="test-only-placeholder")


def fleet_app():
    from rail_vehicle.pages.fleet_comparison import render
    render()


def output_for(payload, eid="batch.B1.accel.lateral.rms"):
    meta = next(item for item in collect_evidence_metadata(payload) if item["evidence_id"] == eid)
    return dict(summary=dict(text="当前数据提供了横向加速度统计，需结合测量条件核查。", evidence_ids=[eid],
                             claim_scope={key: meta[key] for key in ("category", "axis", "batch_refs")}),
                observations=[], possible_causes=[], further_checks=[], limitations=[])


class FleetAITests(unittest.TestCase):
    def setUp(self):
        self.entries = page.demo_entries()
        self.result = dict(comparison=compare_vehicles(self.entries), speed_min=0, speed_max=600)

    def test_context_copies_statistics_and_omits_raw_series_private_fields(self):
        result = deepcopy(self.result)
        result["comparison"]["results"][0].update(label="PRIVATE_CODE", batch_id=123456789, filename="PRIVATE_FILE")
        result["comparison"]["results"][0]["vehicle"].update(vehicle_code="PRIVATE_CODE", notes="PRIVATE_NOTE")
        result["comparison"]["results"][0]["mean_speed_kmh"] = 123.456
        before = deepcopy(result)
        payload = build_fleet_comparison_payload(result)
        text = json.dumps(payload)
        self.assertEqual(payload["analysis_type"], "fleet_comparison")
        for marker in ("PRIVATE_", "123456789", '"series"', '"vehicle_code"', '"batch_id"'):
            self.assertNotIn(marker, text)
        self.assertEqual(next(item["value"] for item in payload["batches"][0]["metrics"] if item["evidence_id"] == "batch.B1.speed.mean"), 123.456)
        self.assertEqual(result, before)

    def test_metadata_keeps_per_vehicle_axis_scope_and_unscoped_parameters(self):
        payload = build_fleet_comparison_payload(self.result)
        catalog = {item["evidence_id"]: item for item in collect_evidence_metadata(payload)}
        self.assertEqual(catalog["comparison.speed.minimum"]["batch_refs"], [])
        for ref in ("B1", "B2"):
            item = catalog[f"batch.{ref}.accel.lateral.rms"]
            self.assertEqual((item["category"], item["axis"], item["batch_refs"]), ("batch_metric", "lateral", [ref]))

    def test_six_vehicles_have_distinct_scope_and_undefined_sampling_is_omitted(self):
        entries = []
        for index in range(6):
            entry = deepcopy(self.entries[index % 2])
            entry["vehicle"]["id"] = index + 1
            entries.append(entry)
        result = dict(comparison=compare_vehicles(entries), speed_min=0, speed_max=600)
        result["comparison"]["results"][0]["sampling_summary"]["interval_median_seconds"] = None
        payload = build_fleet_comparison_payload(result)
        self.assertEqual(len(payload["batches"]), 6)
        catalog = {item["evidence_id"]: item for item in collect_evidence_metadata(payload)}
        self.assertNotIn("batch.B1.sampling.interval_median_seconds", catalog)
        for index in range(1, 7):
            self.assertEqual(catalog[f"batch.B{index}.accel.vertical.rms"]["batch_refs"], [f"B{index}"])

    def test_unknown_and_mismatched_scope_still_rejected(self):
        payload = build_fleet_comparison_payload(self.result)
        self.assertEqual(validate_output(output_for(payload), payload)["summary"]["claim_scope"]["batch_refs"], ["B1"])
        cases = [
            ("evidence_id_unknown", lambda claim: claim.update(evidence_ids=["unknown.id"])),
            ("evidence_scope_category_mismatch", lambda claim: claim["claim_scope"].update(category="metric")),
            ("evidence_scope_axis_mismatch", lambda claim: claim["claim_scope"].update(axis="vertical")),
            ("evidence_scope_batch_mismatch", lambda claim: claim["claim_scope"].update(batch_refs=["B1", "B2"])),
            ("claim_scope_schema_invalid", lambda claim: claim.pop("claim_scope")),
            ("claim_scope_schema_invalid", lambda claim: claim.update(claim_scope=None)),
        ]
        for code, mutate in cases:
            with self.subTest(code=code):
                output = output_for(payload)
                mutate(output["summary"])
                with self.assertRaises(AIContractError) as error:
                    validate_output(output, payload)
                self.assertEqual(error.exception.code, code)

    def test_both_mocked_providers_send_same_fleet_constraints(self):
        payload = build_fleet_comparison_payload(self.result)
        for provider_type, name in ((DeepSeekResponsesProvider, "deepseek"), (OpenAIResponsesProvider, "openai")):
            with self.subTest(provider=name):
                responses = Mock()
                responses.create.return_value = SimpleNamespace(status="completed", output_text=json.dumps(output_for(payload)))
                provider = provider_type(AIConfig(enabled=True, provider=name, model="mock-model", api_key="test-only-placeholder"), client=SimpleNamespace(responses=responses))
                result = AIAnalysisService(provider).analyze(payload)
                self.assertEqual((result["status"], result["scope_validation"]), ("available", "verified"))
                request = responses.create.call_args.kwargs
                text = "".join(c["text"] for m in request["input"] for c in m["content"])
                self.assertIn(build_evidence_reference_instructions(payload), text)
                self.assertIn("不作车辆优劣", text)
                responses.create.assert_called_once()

    def app(self):
        app = AppTest.from_function(fleet_app)
        app.session_state["fleet_source"] = "内置虚构对比"
        return app.run()

    def test_ai_is_manual_and_cached_comparison_survives_ai_rerun(self):
        seen = []
        class Provider:
            def analyze(self, payload):
                seen.append(payload)
                return output_for(payload)
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=Provider()) as factory:
            app = self.app()
            self.assertEqual(len(app.button), 1)
            app.button[0].click().run()
            factory.assert_not_called()
            self.assertEqual(len(app.dataframe), 2)
            app.button(key="fleet_experiment_ai_generate").click().run()
            self.assertFalse(app.exception)
            factory.assert_called_once()
            self.assertEqual(seen[0]["analysis_type"], "fleet_comparison")
            self.assertTrue(any("横向加速度统计" in item.value for item in app.text))
            self.assertEqual(app.session_state["experiment_fleet_ai"]["scope_validation"], "verified")
            app.run()
            factory.assert_called_once()
            self.assertEqual(len(app.get("plotly_chart")), 3)

    def test_changed_band_or_raw_sample_invalidates_comparison_and_ai(self):
        entries = deepcopy(self.entries)
        with patch.object(page, "demo_entries", side_effect=lambda: deepcopy(entries)), \
             patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=FakeProvider(output_for(build_fleet_comparison_payload(self.result)))):
            app = self.app()
            app.button[0].click().run()
            app.button(key="fleet_experiment_ai_generate").click().run()
            self.assertIn("experiment_fleet_ai", app.session_state)
            entries[0]["samples"][0]["lateral_accel"] += 0.01
            app.run()
            self.assertNotIn("experiment_fleet_ai", app.session_state)
            self.assertEqual(len(app.dataframe), 0)
            app.button[0].click().run()
            app.button(key="fleet_experiment_ai_generate").click().run()
            app.slider[0].set_value((70, 80)).run()
            self.assertNotIn("experiment_fleet_ai", app.session_state)
            self.assertEqual(len(app.get("plotly_chart")), 0)

    def test_disabled_and_rejected_ai_do_not_hide_deterministic_results(self):
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=AIConfig(enabled=False)), \
             patch.object(experiment_ai, "create_configured_provider") as factory:
            app = self.app()
            app.button[0].click().run()
            self.assertTrue(app.button(key="fleet_experiment_ai_generate").disabled)
            self.assertEqual(len(app.dataframe), 2)
            factory.assert_not_called()
        class BadProvider:
            def analyze(self, payload):
                output = output_for(payload)
                output["summary"]["evidence_ids"] = ["unknown.id"]
                return output
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=BadProvider()):
            app = self.app()
            app.button[0].click().run()
            app.button(key="fleet_experiment_ai_generate").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("未知证据" in item.value for item in app.warning))
            self.assertNotIn("experiment_fleet_ai", app.session_state)
            self.assertEqual(len(app.dataframe), 2)
            self.assertFalse(any("横向加速度统计" in item.value for item in app.text))

    def test_public_mode_blocks_ai_and_keeps_comparison(self):
        import os
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}), \
             patch.object(experiment_ai.AIConfig, "from_sources", side_effect=AssertionError("no config")), \
             patch.object(experiment_ai, "create_configured_provider") as factory:
            app = self.app()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe), 2)
            self.assertFalse(any("AI" in button.label for button in app.button))
            factory.assert_not_called()
