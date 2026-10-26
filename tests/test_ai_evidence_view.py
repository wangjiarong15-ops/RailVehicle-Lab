"""Evidence drill-down copies facts, preserves strict validation and privacy."""

import copy
import json
import unittest

from streamlit.testing.v1 import AppTest

from rail_vehicle.ai.evidence_view import evidence_detail_catalog
from rail_vehicle.ai.provider import input_fingerprint
from rail_vehicle.ai.ui import render_validated_result
from test_ai_ui import single_payload, comparison_payload, valid_response, RenderRecorder


class EvidenceViewTests(unittest.TestCase):
    def test_metric_uses_current_value_and_validator_metadata_without_mutation(self):
        payload = single_payload()
        payload["metrics"][0]["value"] = 123.456
        before = copy.deepcopy(payload)
        row = evidence_detail_catalog(payload)["metric.speed.mean"][0]
        self.assertEqual(row["值"], "123.456")
        self.assertEqual(row["类别"], "metric")
        self.assertEqual(row["轴向"], "not_applicable")
        self.assertEqual(row["批次范围"], "B1")
        self.assertEqual(row["单位"], "km/h")
        self.assertEqual(payload, before)

    def test_threshold_and_event_details_keep_original_units_and_time_definition(self):
        payload = single_payload()
        catalog = evidence_detail_catalog(payload)
        threshold = catalog["metric.exceedance.lateral"]
        self.assertEqual([row["单位"] for row in threshold], ["点", "%"])
        self.assertTrue(all(row["类别"] == "threshold_statistic" for row in threshold))
        event = catalog["event.lateral.001"]
        self.assertEqual(len(event), 5)
        self.assertEqual(event[2]["值"], str(payload["anomaly_events"][0]["duration_seconds"]))
        self.assertIn("不表示", event[2]["口径"])
        self.assertEqual(event[0]["轴向"], "lateral")

    def test_comparison_batch_scopes_and_request_values_never_mix(self):
        context = comparison_payload()
        first = evidence_detail_catalog(context)
        self.assertEqual(first["batch.B1.speed.mean"][0]["批次范围"], "B1")
        self.assertEqual(first["batch.B2.speed.mean"][0]["批次范围"], "B2")
        self.assertEqual(first["batch.B2.speed.mean"][0]["类别"], "batch_metric")
        context["batches"][1]["metrics"][1]["value"] = 99
        second = evidence_detail_catalog(context)
        self.assertEqual(second["batch.B2.speed.mean"][0]["值"], "99")
        self.assertNotEqual(first["batch.B2.speed.mean"], second["batch.B2.speed.mean"])

    def test_five_claim_groups_show_only_cited_facts_and_ignore_extra_private_metadata(self):
        payload = single_payload()
        result = dict(analysis=valid_response(payload), input_fingerprint=input_fingerprint(payload),
                      scope_validation="verified", private_note="PRIVATE_SENTINEL")
        recorder = RenderRecorder()
        self.assertTrue(render_validated_result(result, payload, recorder))
        tables = [value for name, value in recorder.calls if name == "dataframe"]
        self.assertEqual(len(tables), 5)
        for table in tables:
            self.assertEqual({row["证据 ID"] for row in table}, {"metric.speed.mean"})
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(recorder.calls, ensure_ascii=False))

    def test_rejected_or_stale_output_has_no_text_or_evidence_expanders(self):
        payload = single_payload()
        for state in ("unknown", "mismatch", "stale", "missing"):
            with self.subTest(state=state):
                result = dict(analysis=valid_response(payload), input_fingerprint=input_fingerprint(payload), scope_validation="verified")
                if state == "unknown":
                    result["analysis"]["summary"]["evidence_ids"] = ["unknown.id"]
                elif state == "mismatch":
                    result["analysis"]["summary"]["claim_scope"]["axis"] = "vertical"
                elif state == "stale":
                    result["input_fingerprint"] = "stale"
                else:
                    result["analysis"]["summary"].pop("claim_scope")
                recorder = RenderRecorder()
                self.assertFalse(render_validated_result(result, payload, recorder))
                self.assertFalse(any(name in ("text", "expander", "dataframe") for name, _ in recorder.calls))

    def test_legacy_scope_is_explicitly_unverified_per_claim(self):
        payload = single_payload()
        output = valid_response(payload)
        output["summary"]["claim_scope"] = None
        recorder = RenderRecorder()
        self.assertTrue(render_validated_result(dict(analysis=output, input_fingerprint=input_fingerprint(payload)), payload, recorder))
        self.assertTrue(any("本条结论" in str(value) and "未验证" in str(value) for name, value in recorder.calls if name == "warning"))

    def test_streamlit_shows_actual_evidence_values_without_provider(self):
        def page():
            import streamlit as st
            from rail_vehicle.ai.ui import render_validated_result
            render_validated_result(st.session_state["result"], st.session_state["payload"], st)
        payload = single_payload()
        app = AppTest.from_function(page)
        app.session_state["payload"] = payload
        app.session_state["result"] = dict(analysis=valid_response(payload), input_fingerprint=input_fingerprint(payload), scope_validation="verified")
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.expander), 5)
        self.assertEqual(len(app.dataframe), 5)
        self.assertEqual(app.dataframe[0].value.iloc[0]["值"], str(payload["metrics"][0]["value"]))
