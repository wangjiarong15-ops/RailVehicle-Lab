"""Tests for the provider-neutral AI analysis contract and payload builders."""

import copy
import json
import unittest

from rail_vehicle.ai import (
    AIAnalysisService,
    AIContractError,
    FakeProvider,
    build_batch_comparison_payload,
    build_single_batch_payload,
    collect_evidence_ids,
    input_fingerprint,
    is_result_current,
    validate_output,
)
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.dynamics import analyze_run_data


def sample(timestamp, speed, lateral, vertical):
    return {
        "timestamp": timestamp,
        "speed_kmh": speed,
        "lateral_accel": lateral,
        "vertical_accel": vertical,
    }


def make_single_payload(with_events=True):
    samples = [
        sample("2026-10-08T09:00:00+00:00", 40, 0.2, -0.1),
        sample("2026-10-08T09:00:01+00:00", 45, 1.8 if with_events else 0.4, 0.2),
        sample("2026-10-08T09:00:02+00:00", 43, 0.3, 0.1),
    ]
    analysis = analyze_run_data(samples, 1.0)
    anomaly_result = detect_anomalies(samples, 1.0)
    vehicle = {
        "vehicle_code": "SECRET-ID-42",
        "vehicle_type": "测试车型",
        "mass_kg": 120000,
        "axle_count": 8,
        "bogie_count": 4,
        "max_speed_kmh": 160,
        "notes": "PRIVATE NOTE",
    }
    return build_single_batch_payload(
        analysis=analysis,
        anomalies=anomaly_result,
        vehicle=vehicle,
        batch_ref="B1",
        selected_start="2026-10-08T09:00:00+00:00",
        selected_end="2026-10-08T09:00:02+00:00",
    )


def valid_response(payload):
    evidence = sorted(collect_evidence_ids(payload))
    return {
        "summary": {"text": "已提供运行统计结果。", "evidence_ids": [evidence[0]]},
        "observations": [],
        "possible_causes": [],
        "further_checks": [],
        "limitations": [],
    }


class AIInfrastructureTests(unittest.TestCase):
    def test_single_batch_payload_uses_calculated_statistics_and_expected_fields(self):
        payload = make_single_payload()
        self.assertEqual(payload["analysis_type"], "single_batch")
        self.assertEqual(payload["sample_count"], 3)
        self.assertIn("metric.speed.mean", collect_evidence_ids(payload))
        self.assertIn("metric.accel.lateral.rms", collect_evidence_ids(payload))
        self.assertIn("metric.accel.vertical.rms", collect_evidence_ids(payload))

    def test_input_omits_vehicle_id_notes_samples_and_source_names(self):
        encoded = json.dumps(make_single_payload(), ensure_ascii=False)
        for secret in ("SECRET-ID-42", "PRIVATE NOTE", "\"speed_kmh\":", "filename", "\"samples\":"):
            self.assertNotIn(secret, encoded)

    def test_event_evidence_ids_are_stable_and_axis_specific(self):
        payload = make_single_payload()
        self.assertEqual([event["evidence_id"] for event in payload["anomaly_events"]], ["event.lateral.001"])
        self.assertEqual(payload["anomaly_events"][0]["type"], "lateral")

    def test_no_anomaly_events_is_a_valid_empty_list(self):
        payload = make_single_payload(with_events=False)
        self.assertEqual(payload["anomaly_events"], [])
        self.assertNotIn("event.lateral.001", collect_evidence_ids(payload))

    def test_batch_comparison_payload_uses_python_results_and_hides_series_and_ids(self):
        samples_a = [sample("2026-10-01T00:00:00+00:00", 20, 0.1, 0.2), sample("2026-10-01T00:00:02+00:00", 30, 0.3, 0.4)]
        samples_b = [sample("2026-10-03T00:00:00+00:00", 35, 0.4, 0.5), sample("2026-10-03T00:00:04+00:00", 40, 0.6, 0.7)]
        comparisons = compare_run_batches([
            {"batch_id": 501, "label": "source label one", "samples": samples_a},
            {"batch_id": 502, "label": "source label two", "samples": samples_b},
        ])
        payload = build_batch_comparison_payload(comparisons)
        self.assertEqual(payload["analysis_type"], "batch_comparison")
        self.assertEqual(len(payload["batches"]), 2)
        self.assertIn("batch.B1.speed.mean", collect_evidence_ids(payload))
        self.assertIn("batch.B2.accel.vertical.peak_absolute", collect_evidence_ids(payload))
        encoded = json.dumps(payload)
        for secret in ("series", "source label", "501", "502", "samples"):
            self.assertNotIn(secret, encoded)

    def test_valid_structured_ai_output_is_accepted(self):
        payload = make_single_payload()
        result = validate_output(valid_response(payload), payload)
        self.assertEqual(result["summary"]["evidence_ids"], valid_response(payload)["summary"]["evidence_ids"])

    def test_unknown_evidence_id_is_rejected(self):
        payload = make_single_payload()
        response = valid_response(payload)
        response["summary"]["evidence_ids"] = ["metric.not-real"]
        with self.assertRaisesRegex(AIContractError, "不存在"):
            validate_output(response, payload)

    def test_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(AIContractError, "有效 JSON"):
            validate_output("{not json", make_single_payload())

    def test_missing_output_field_is_rejected(self):
        response = valid_response(make_single_payload())
        del response["limitations"]
        with self.assertRaisesRegex(AIContractError, "Schema"):
            validate_output(response, make_single_payload())

    def test_non_finite_output_number_is_rejected(self):
        payload = make_single_payload()
        response = valid_response(payload)
        response["summary"]["text"] = "平均速度为 NaN。"
        response["observations"] = [{
            "text": "测得某统计值。", "evidence_ids": ["metric.speed.mean"], "value": float("nan"),
        }]
        with self.assertRaisesRegex(AIContractError, "Schema|非有限"):
            validate_output(response, payload)

    def test_unqualified_or_fault_assertion_output_is_rejected(self):
        payload = make_single_payload()
        response = valid_response(payload)
        response["possible_causes"] = [{
            "text": "车辆故障。", "evidence_ids": ["metric.speed.mean"], "qualification": "hypothesis",
        }]
        with self.assertRaisesRegex(AIContractError, "故障"):
            validate_output(response, payload)

    def test_fake_provider_service_returns_validated_result_without_network(self):
        payload = make_single_payload()
        provider = FakeProvider(json.dumps(valid_response(payload), ensure_ascii=False))
        result = AIAnalysisService(provider).analyze(payload)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result["analysis"], valid_response(payload))
        self.assertEqual(result["input_fingerprint"], input_fingerprint(payload))

    def test_changed_input_invalidates_previous_result(self):
        payload = make_single_payload()
        provider = FakeProvider(valid_response(payload))
        result = AIAnalysisService(provider).analyze(payload)
        changed_payload = copy.deepcopy(payload)
        changed_payload["threshold"]["value"] += 0.5
        self.assertTrue(is_result_current(result, payload))
        self.assertFalse(is_result_current(result, changed_payload))


if __name__ == "__main__":
    unittest.main()
