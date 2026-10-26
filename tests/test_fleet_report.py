"""Actual fleet PDFs, strict optional AI boundaries and session invalidation."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.fleet_context import build_fleet_comparison_payload
from rail_vehicle.ai.provider import AIAnalysisService, FakeProvider
from rail_vehicle.ai.ui import selection_fingerprint
from rail_vehicle.experiment_report import generate_experiment_pdf
import rail_vehicle.experiment_report as reports
from rail_vehicle.fleet_comparison import compare_vehicles
from rail_vehicle.fleet_report import build_fleet_report
from rail_vehicle.pages import fleet_comparison as page, experiment_ai


NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
FINGERPRINT = "a" * 64
SELECTION = dict(source="demo")
READY = AIConfig(enabled=True, provider="deepseek", model="offline-mock-model", api_key="test-only-placeholder")


def output_for(payload):
    from rail_vehicle.ai.schemas import collect_evidence_metadata
    meta = next(item for item in collect_evidence_metadata(payload) if item["evidence_id"] == "batch.B1.accel.lateral.rms")
    claim = dict(text="离线 Mock：统计表现需结合工况核实。", evidence_ids=[meta["evidence_id"]],
                 claim_scope={key: meta[key] for key in ("category", "axis", "batch_refs")})
    return dict(summary=deepcopy(claim), observations=[deepcopy(claim)],
                possible_causes=[dict(deepcopy(claim), qualification="hypothesis")],
                further_checks=[dict(action="核对测量条件。", reason="线路与载荷尚未核实。", evidence_ids=claim["evidence_ids"], claim_scope=claim["claim_scope"])],
                limitations=[deepcopy(claim)])


def cached(payload):
    result = AIAnalysisService(FakeProvider(output_for(payload))).analyze(payload)
    result["selection_fingerprint"] = selection_fingerprint(dict(selection=SELECTION, analysis_fingerprint=FINGERPRINT))
    result["ai_metadata"] = dict(provider="deepseek", model="offline-mock-model", generated_at=NOW.isoformat())
    return result


def fleet_app():
    from rail_vehicle.pages.fleet_comparison import render
    render()


class FleetReportTests(unittest.TestCase):
    def setUp(self):
        self.entries = page.demo_entries()
        self.result = dict(comparison=compare_vehicles(self.entries), speed_min=0, speed_max=600)

    def report(self, **kwargs):
        return build_fleet_report(self.result, FINGERPRINT, SELECTION, generated_at=NOW, **kwargs)

    def test_copies_statistics_ranges_metadata_without_recalculation_or_mutation(self):
        self.result["comparison"]["results"][0]["mean_speed_kmh"] = 123.456
        before = deepcopy(self.result)
        report = self.report()
        text = json.dumps(report, ensure_ascii=False)
        self.assertIn("123.456", text)
        self.assertIn(self.result["comparison"]["results"][0]["selected_start_utc"], text)
        self.assertIn("虚构演示-1", text)
        self.assertEqual(len(report["charts"]), 3)
        self.assertEqual(self.result, before)
        self.assertEqual(report, self.report())

    def test_chart_series_preserve_actual_gaps_and_are_scatter_only(self):
        rows = [dict(timestamp=f"2026-10-26T00:00:0{i}+00:00", speed_kmh=speed, lateral_accel=.2, vertical_accel=.3)
                for i, speed in enumerate((70, 90, 80))]
        for entry in self.entries:
            entry["samples"] = deepcopy(rows)
        result = dict(comparison=compare_vehicles(self.entries, 70, 80), speed_min=70, speed_max=80)
        report = build_fleet_report(result, FINGERPRINT, SELECTION)
        chart = report["charts"][0]
        for series in chart["series"]:
            self.assertEqual([point[0] for point in series["points"]], [0, 2])
        drawing = reports._drawing(chart, reports._font_names()[0])
        from reportlab.graphics.charts.lineplots import LinePlot
        plot = next(item for item in drawing.contents if isinstance(item, LinePlot))
        self.assertEqual(plot.joinedLines, 0)
        self.assertEqual(len(plot.data), 2)
        with patch.object(reports, "KeepTogether", wraps=reports.KeepTogether) as groups:
            self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))
        self.assertTrue(any(call.args[0][0].getPlainText() == "四、多车辆对比曲线" for call in groups.call_args_list))

    def test_actual_deterministic_pdf_with_one_sample_and_six_vehicles(self):
        entries = []
        for index in range(6):
            entry = deepcopy(self.entries[index % 2])
            entry["vehicle"]["id"] = index + 1
            entry["samples"] = entry["samples"][:1]
            entries.append(entry)
        report = build_fleet_report(dict(comparison=compare_vehicles(entries), speed_min=0, speed_max=600), FINGERPRINT, SELECTION)
        self.assertEqual(len(report["tables"]), 8)
        self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))

    def test_optional_verified_ai_is_copied_with_evidence_and_safe_metadata(self):
        payload = build_fleet_comparison_payload(self.result)
        result = cached(payload)
        result["ai_metadata"]["api_key"] = "PRIVATE_SENTINEL"
        result["raw_response"] = "PRIVATE_BODY"
        report = self.report(ai_context=payload, ai_result=result)
        self.assertEqual(report["ai"]["scope_validation"], "verified")
        self.assertEqual(report["ai"]["evidence"][0]["batch_refs"], ["B1"])
        self.assertNotIn("PRIVATE_", json.dumps(report))
        self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))
        self.assertNotEqual(report["report_fingerprint"], self.report()["report_fingerprint"])

    def test_unavailable_stale_and_failed_ai_are_omitted_without_blocking_pdf(self):
        payload = build_fleet_comparison_payload(self.result)
        mutations = [lambda r: r.update(status="unavailable"), lambda r: r.update(status="validation_failed"),
                     lambda r: r.update(input_fingerprint="b"*64), lambda r: r.update(selection_fingerprint="stale"),
                     lambda r: r.update(scope_validation="legacy_unverified")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                result = cached(payload)
                mutate(result)
                report = self.report(ai_context=payload, ai_result=result)
                self.assertIsNone(report["ai"])
                self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))

    def test_unknown_scope_missing_null_and_mismatched_evidence_are_rechecked(self):
        payload = build_fleet_comparison_payload(self.result)
        mutations = [lambda c: c.update(evidence_ids=["unknown.id"]), lambda c: c.pop("claim_scope"),
                     lambda c: c.update(claim_scope=None), lambda c: c["claim_scope"].update(category="metric"),
                     lambda c: c["claim_scope"].update(axis="vertical"), lambda c: c["claim_scope"].update(batch_refs=["B2"])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                result = cached(payload)
                mutate(result["analysis"]["summary"])
                self.assertIsNone(self.report(ai_context=payload, ai_result=result)["ai"])

    def test_history_report_omits_arbitrary_fields_and_rejects_invalid_identity(self):
        self.result["api_key"] = "PRIVATE_SENTINEL"
        self.result["comparison"]["results"][0]["filename"] = "PRIVATE_FILE"
        self.result["comparison"]["results"][0]["vehicle"]["remarks"] = "PRIVATE_NOTE"
        report = build_fleet_report(self.result, FINGERPRINT, dict(source="history", api_key="PRIVATE_SECRET"))
        self.assertNotIn("PRIVATE_", json.dumps(report))
        for fingerprint, selection in (("bad", SELECTION), (FINGERPRINT, dict(source="unknown"))):
            with self.subTest(fingerprint=fingerprint), self.assertRaises(ValueError):
                build_fleet_report(self.result, fingerprint, selection)

    def test_ai_layout_failure_falls_back_without_mutating_report(self):
        payload = build_fleet_comparison_payload(self.result)
        report = self.report(ai_context=payload, ai_result=cached(payload))
        original = deepcopy(report)
        append = reports._append_experiment_ai
        def fail_only_ai(story, section, *args):
            if section is not None:
                raise ValueError("PRIVATE_BODY")
            return append(story, section, *args)
        with patch.object(reports, "_append_experiment_ai", side_effect=fail_only_ai):
            self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))
        self.assertEqual(report, original)

    def app(self):
        app = AppTest.from_function(fleet_app)
        app.session_state["fleet_source"] = "内置虚构对比"
        return app.run()

    def test_public_pdf_generation_uses_cached_statistics_without_ai_or_recompute(self):
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}), \
             patch.object(experiment_ai.AIConfig, "from_sources", side_effect=AssertionError("no config")), \
             patch.object(page, "compare_vehicles", wraps=compare_vehicles) as compute:
            app = self.app()
            app.button[0].click().run()
            app.button(key="fleet_report_generate").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state[page.STATE_KEY]["pdf"].startswith(b"%PDF-"))
            compute.assert_called_once()
            app.slider[0].set_value((70, 80)).run()
            self.assertNotIn(page.STATE_KEY, app.session_state)
            self.assertEqual(len(app.get("download_button")), 0)

    def test_ai_success_and_failure_invalidate_old_pdf_without_extra_requests(self):
        class Provider:
            fail = False
            def analyze(self, payload):
                output = output_for(payload)
                if self.fail:
                    output["summary"]["evidence_ids"] = ["unknown.id"]
                return output
        provider = Provider()
        with patch.object(experiment_ai.AIConfig, "from_sources", return_value=READY), \
             patch.object(experiment_ai, "create_configured_provider", return_value=provider) as factory:
            app = self.app()
            app.button[0].click().run()
            app.button(key="fleet_report_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 1)
            factory.assert_not_called()
            app.button(key="fleet_experiment_ai_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 0)
            app.button(key="fleet_report_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 1)
            factory.assert_called_once()
            provider.fail = True
            app.button(key="fleet_experiment_ai_generate").click().run()
            self.assertEqual(len(app.get("download_button")), 0)
            app.button(key="fleet_report_generate").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("download_button")), 1)
            self.assertEqual(factory.call_count, 2)
