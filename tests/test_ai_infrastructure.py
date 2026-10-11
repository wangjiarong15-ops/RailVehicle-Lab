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

    def test_complete_output_with_all_claim_categories_is_accepted(self):
        payload = make_single_payload()
        response = {
            "summary": {"text": "当前区间包含已计算的运行统计。", "evidence_ids": ["metric.speed.mean"]},
            "observations": [{
                "text": "横向加速度 RMS 可用于描述该区间的波动水平。",
                "evidence_ids": ["metric.accel.lateral.rms"],
            }],
            "possible_causes": [{
                "text": "该波动可能与运行条件变化有关。",
                "evidence_ids": ["event.lateral.001"],
                "qualification": "hypothesis",
            }],
            "further_checks": [{
                "action": "核对对应区间的运行记录。",
                "reason": "确认异常事件发生时的上下文。",
                "evidence_ids": ["metric.accel.vertical.rms"],
            }],
            "limitations": [{
                "text": "该统计仅覆盖当前筛选区间。",
                "evidence_ids": ["metric.speed.maximum"],
            }],
        }

        self.assertEqual(validate_output(response, payload), response)

    def test_possible_cause_requires_hypothesis_qualification(self):
        payload = make_single_payload()
        response = valid_response(payload)
        response["possible_causes"] = [{
            "text": "可能与运行条件变化有关。",
            "evidence_ids": ["event.lateral.001"],
        }]

        with self.assertRaises(AIContractError) as raised:
            validate_output(response, payload)
        self.assertEqual(raised.exception.code, "output_schema_invalid")

    def test_possible_cause_rejects_non_hypothesis_qualification(self):
        payload = make_single_payload()
        response = valid_response(payload)
        response["possible_causes"] = [{
            "text": "可能与运行条件变化有关。",
            "evidence_ids": ["event.lateral.001"],
            "qualification": "confirmed",
        }]

        with self.assertRaises(AIContractError) as raised:
            validate_output(response, payload)
        self.assertEqual(raised.exception.code, "output_schema_invalid")

    def test_further_check_requires_action_and_reason(self):
        payload = make_single_payload()
        for missing_field in ("action", "reason"):
            with self.subTest(missing_field=missing_field):
                response = valid_response(payload)
                check = {
                    "action": "核对运行记录。",
                    "reason": "确认对应时间范围的上下文。",
                    "evidence_ids": ["metric.speed.mean"],
                }
                del check[missing_field]
                response["further_checks"] = [check]

                with self.assertRaises(AIContractError) as raised:
                    validate_output(response, payload)
                self.assertEqual(raised.exception.code, "output_schema_invalid")

    def test_unknown_evidence_id_is_rejected(self):
        payload = make_single_payload()
        response = valid_response(payload)
        response["summary"]["evidence_ids"] = ["metric.not-real"]
        with self.assertRaisesRegex(AIContractError, "不存在"):
            validate_output(response, payload)

    def test_unknown_evidence_id_is_rejected_in_every_claim_category(self):
        payload = make_single_payload()
        unknown = "metric.not-real"
        cases = {
            "summary": lambda response: response["summary"].update(evidence_ids=[unknown]),
            "observations": lambda response: response["observations"].append(
                {"text": "统计观察。", "evidence_ids": [unknown]}
            ),
            "possible_causes": lambda response: response["possible_causes"].append(
                {"text": "原因假设。", "evidence_ids": [unknown], "qualification": "hypothesis"}
            ),
            "further_checks": lambda response: response["further_checks"].append(
                {"action": "进一步核对。", "reason": "确认记录。", "evidence_ids": [unknown]}
            ),
            "limitations": lambda response: response["limitations"].append(
                {"text": "输入范围有限。", "evidence_ids": [unknown]}
            ),
        }
        for category, mutate in cases.items():
            with self.subTest(category=category):
                response = valid_response(payload)
                mutate(response)
                with self.assertRaisesRegex(AIContractError, "不存在"):
                    validate_output(response, payload)

    def test_duplicate_evidence_id_with_different_records_is_rejected(self):
        payload = make_single_payload()
        original = payload["metrics"][0]
        payload["metrics"].append({
            **original,
            "name": "不同指标记录",
            "value": original["value"] + 10,
        })
        with self.assertRaisesRegex(AIContractError, "重复 evidence_id"):
            validate_output(valid_response(payload), payload)

    def test_existing_evidence_with_axis_mismatch_is_rejected_when_scope_is_declared(self):
        payload = make_single_payload()
        vertical_metric = "metric.accel.vertical.rms"
        self.assertIn(vertical_metric, collect_evidence_ids(payload))
        response = valid_response(payload)
        response["observations"] = [{
            "text": "横向加速度统计观察。",
            "evidence_ids": [vertical_metric],
            "claim_scope": {
                "category": "metric",
                "axis": "lateral",
                "batch_refs": ["B1"],
            },
        }]
        with self.assertRaisesRegex(AIContractError, "axis"):
            validate_output(response, payload)

    def test_existing_evidence_with_category_mismatch_is_rejected(self):
        payload = make_single_payload()
        evidence_id = "metric.speed.mean"
        self.assertIn(evidence_id, collect_evidence_ids(payload))
        response = valid_response(payload)
        response["observations"] = [{
            "text": "异常事件观察。",
            "evidence_ids": [evidence_id],
            "claim_scope": {
                "category": "event",
                "axis": "not_applicable",
                "batch_refs": ["B1"],
            },
        }]
        with self.assertRaisesRegex(AIContractError, "category"):
            validate_output(response, payload)

    def test_existing_evidence_with_batch_scope_mismatch_is_rejected(self):
        payload = make_single_payload()
        evidence_id = "metric.speed.mean"
        self.assertIn(evidence_id, collect_evidence_ids(payload))
        response = valid_response(payload)
        response["observations"] = [{
            "text": "批次速度统计观察。",
            "evidence_ids": [evidence_id],
            "claim_scope": {
                "category": "metric",
                "axis": "not_applicable",
                "batch_refs": ["B2"],
            },
        }]
        with self.assertRaisesRegex(AIContractError, "batch_refs"):
            validate_output(response, payload)

    def test_comparison_evidence_cannot_be_declared_as_another_batch(self):
        samples_a = [sample("2026-10-01T00:00:00+00:00", 20, 0.1, 0.2), sample("2026-10-01T00:00:02+00:00", 30, 0.3, 0.4)]
        samples_b = [sample("2026-10-03T00:00:00+00:00", 35, 0.4, 0.5), sample("2026-10-03T00:00:04+00:00", 40, 0.6, 0.7)]
        comparisons = compare_run_batches([
            {"batch_id": 501, "label": "one", "samples": samples_a},
            {"batch_id": 502, "label": "two", "samples": samples_b},
        ])
        payload = build_batch_comparison_payload(comparisons)
        response = valid_response(payload)
        response["summary"]["evidence_ids"] = ["batch.B1.speed.mean"]
        response["summary"]["claim_scope"] = {
            "category": "batch_metric",
            "axis": "not_applicable",
            "batch_refs": ["B2"],
        }
        with self.assertRaisesRegex(AIContractError, "batch_refs"):
            validate_output(response, payload)

    def test_matching_structured_evidence_scope_is_accepted(self):
        payload = make_single_payload()
        evidence_id = "metric.accel.lateral.rms"
        response = valid_response(payload)
        response["observations"] = [{
            "text": "横向加速度统计观察。",
            "evidence_ids": [evidence_id],
            "claim_scope": {
                "category": "metric",
                "axis": "lateral",
                "batch_refs": ["B1"],
            },
        }]
        self.assertEqual(validate_output(response, payload)["observations"][0]["claim_scope"]["axis"], "lateral")

    def test_mixed_category_and_axis_scope_follow_existing_aggregation_rules(self):
        payload = make_single_payload()
        response = valid_response(payload)
        response["summary"] = {
            "text": "引用了多个已计算证据类别和轴向。",
            "evidence_ids": [
                "metric.speed.mean",
                "metric.accel.lateral.rms",
                "metric.accel.vertical.rms",
                "event.lateral.001",
            ],
            "claim_scope": {
                "category": "mixed",
                "axis": "mixed",
                "batch_refs": ["B1"],
            },
        }

        validated = validate_output(response, payload)
        self.assertEqual(validated["summary"]["claim_scope"]["category"], "mixed")
        self.assertEqual(validated["summary"]["claim_scope"]["axis"], "mixed")

    def test_claim_scope_missing_or_null_keeps_legacy_evidence_validation(self):
        payload = make_single_payload()
        for scope_state in ("missing", "null"):
            with self.subTest(scope_state=scope_state):
                response = valid_response(payload)
                response["summary"]["evidence_ids"] = ["metric.accel.vertical.rms"]
                if scope_state == "null":
                    response["summary"]["claim_scope"] = None
                validated = validate_output(response, payload)
                self.assertEqual(validated["summary"]["evidence_ids"], ["metric.accel.vertical.rms"])

    def test_claim_scope_empty_object_and_wrong_types_are_rejected(self):
        payload = make_single_payload()
        malformed_scopes = ({}, "metric", ["metric", "lateral", ["B1"]])
        for scope in malformed_scopes:
            with self.subTest(scope=scope):
                response = valid_response(payload)
                response["summary"]["claim_scope"] = scope
                with self.assertRaises(AIContractError) as raised:
                    validate_output(response, payload)
                self.assertEqual(raised.exception.code, "claim_scope_schema_invalid")

    def test_claim_scope_missing_each_required_field_is_rejected(self):
        payload = make_single_payload()
        valid_scope = {"category": "metric", "axis": "not_applicable", "batch_refs": ["B1"]}
        for field in valid_scope:
            with self.subTest(missing_field=field):
                response = valid_response(payload)
                response["summary"]["claim_scope"] = {
                    key: value for key, value in valid_scope.items() if key != field
                }
                with self.assertRaises(AIContractError) as raised:
                    validate_output(response, payload)
                self.assertEqual(raised.exception.code, "claim_scope_schema_invalid")

    def test_claim_scope_invalid_category_and_axis_values_are_rejected(self):
        payload = make_single_payload()
        base_scope = {"category": "metric", "axis": "not_applicable", "batch_refs": ["B1"]}
        invalid_scopes = (
            {**base_scope, "category": "vehicle_fault"},
            {**base_scope, "axis": "diagonal"},
        )
        for scope in invalid_scopes:
            with self.subTest(scope=scope):
                response = valid_response(payload)
                response["summary"]["claim_scope"] = scope
                with self.assertRaisesRegex(AIContractError, "Schema"):
                    validate_output(response, payload)

    def test_empty_evidence_id_array_is_rejected_in_every_claim_category(self):
        payload = make_single_payload()
        cases = {
            "summary": lambda response: response["summary"].update(evidence_ids=[]),
            "observations": lambda response: response["observations"].append(
                {"text": "观察。", "evidence_ids": []}
            ),
            "possible_causes": lambda response: response["possible_causes"].append(
                {"text": "假设。", "evidence_ids": [], "qualification": "hypothesis"}
            ),
            "further_checks": lambda response: response["further_checks"].append(
                {"action": "复核。", "reason": "核对记录。", "evidence_ids": []}
            ),
            "limitations": lambda response: response["limitations"].append(
                {"text": "范围有限。", "evidence_ids": []}
            ),
        }
        for category, mutate in cases.items():
            with self.subTest(category=category):
                response = valid_response(payload)
                mutate(response)
                with self.assertRaisesRegex(AIContractError, "Schema"):
                    validate_output(response, payload)

    def test_valid_multi_batch_claim_scope_and_evidence_references_are_accepted(self):
        samples_a = [
            sample("2026-10-01T00:00:00+00:00", 20, 0.1, 0.2),
            sample("2026-10-01T00:00:02+00:00", 30, 0.3, 0.4),
        ]
        samples_b = [
            sample("2026-10-03T00:00:00+00:00", 35, 0.4, 0.5),
            sample("2026-10-03T00:00:04+00:00", 40, 0.6, 0.7),
        ]
        comparisons = compare_run_batches([
            {"batch_id": 501, "label": "one", "samples": samples_a},
            {"batch_id": 502, "label": "two", "samples": samples_b},
        ])
        payload = build_batch_comparison_payload(comparisons)
        response = valid_response(payload)
        response["summary"] = {
            "text": "两个批次的速度统计均已引用。",
            "evidence_ids": ["batch.B1.speed.mean", "batch.B2.speed.mean"],
            "claim_scope": {
                "category": "batch_metric",
                "axis": "not_applicable",
                "batch_refs": ["B1", "B2"],
            },
        }
        validated = validate_output(response, payload)
        self.assertEqual(validated["summary"]["claim_scope"]["batch_refs"], ["B1", "B2"])

    def test_batch_refs_missing_or_exceeding_referenced_batches_are_rejected(self):
        samples_a = [
            sample("2026-10-01T00:00:00+00:00", 20, 0.1, 0.2),
            sample("2026-10-01T00:00:02+00:00", 30, 0.3, 0.4),
        ]
        samples_b = [
            sample("2026-10-03T00:00:00+00:00", 35, 0.4, 0.5),
            sample("2026-10-03T00:00:04+00:00", 40, 0.6, 0.7),
        ]
        comparisons = compare_run_batches([
            {"batch_id": 501, "label": "one", "samples": samples_a},
            {"batch_id": 502, "label": "two", "samples": samples_b},
        ])
        payload = build_batch_comparison_payload(comparisons)
        invalid_batch_refs = (["B1"], ["B1", "B2", "B3"])
        for batch_refs in invalid_batch_refs:
            with self.subTest(batch_refs=batch_refs):
                response = valid_response(payload)
                response["summary"] = {
                    "text": "两个批次的速度统计均已引用。",
                    "evidence_ids": ["batch.B1.speed.mean", "batch.B2.speed.mean"],
                    "claim_scope": {
                        "category": "batch_metric",
                        "axis": "not_applicable",
                        "batch_refs": batch_refs,
                    },
                }
                with self.assertRaisesRegex(AIContractError, "batch_refs"):
                    validate_output(response, payload)

    def test_invalid_json_is_rejected(self):
        with self.assertRaisesRegex(AIContractError, "有效 JSON"):
            validate_output("{not json", make_single_payload())

    def test_valid_json_with_non_object_root_is_schema_invalid(self):
        payload = make_single_payload()
        for encoded in ('[]', '"valid JSON string"', '7', 'null'):
            with self.subTest(root_type=encoded):
                with self.assertRaises(AIContractError) as raised:
                    validate_output(encoded, payload)
                self.assertEqual(raised.exception.code, "output_schema_invalid")

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

    def test_output_validation_failures_have_fixed_specific_codes(self):
        payload = make_single_payload()

        cases = []
        cases.append(("output_json_invalid", "{invalid json", payload))

        malformed_scope = valid_response(payload)
        malformed_scope["summary"]["claim_scope"] = {}
        cases.append(("claim_scope_schema_invalid", malformed_scope, payload))

        unknown_evidence = valid_response(payload)
        unknown_evidence["summary"]["evidence_ids"] = ["private-unknown-evidence"]
        cases.append(("evidence_id_unknown", unknown_evidence, payload))

        for code, scope in (
            ("evidence_scope_category_mismatch", {"category": "event", "axis": "not_applicable", "batch_refs": ["B1"]}),
            ("evidence_scope_axis_mismatch", {"category": "metric", "axis": "lateral", "batch_refs": ["B1"]}),
            ("evidence_scope_batch_mismatch", {"category": "metric", "axis": "not_applicable", "batch_refs": ["B2"]}),
        ):
            response = valid_response(payload)
            response["summary"]["evidence_ids"] = ["metric.speed.mean"]
            response["summary"]["claim_scope"] = scope
            cases.append((code, response, payload))

        invalid_schema = valid_response(payload)
        del invalid_schema["limitations"]
        cases.append(("output_schema_invalid", invalid_schema, payload))

        non_finite = valid_response(payload)
        non_finite["summary"]["value"] = float("inf")
        cases.append(("output_non_finite_number", non_finite, payload))

        unsafe = valid_response(payload)
        unsafe["summary"]["text"] = "车辆故障"
        cases.append(("unsafe_claim_text", unsafe, payload))

        for expected_code, response, context in cases:
            with self.subTest(code=expected_code):
                with self.assertRaises(AIContractError) as caught:
                    validate_output(response, context)
                self.assertEqual(caught.exception.code, expected_code)

    def test_unknown_contract_code_falls_back_without_exposing_exception_text(self):
        private_marker = "PRIVATE_MODEL_OUTPUT_AND_EXCEPTION"

        class RaisingProvider:
            def analyze(self, payload):
                raise AIContractError(private_marker, code="unrecognized-provider-code")

        result = AIAnalysisService(RaisingProvider()).analyze(make_single_payload())
        self.assertEqual(result["code"], "output_contract_invalid")
        self.assertNotIn(private_marker, json.dumps(result, ensure_ascii=False))
        self.assertNotIn("unrecognized-provider-code", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
