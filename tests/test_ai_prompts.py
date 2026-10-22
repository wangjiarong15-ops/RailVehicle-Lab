"""Tests for current-request evidence allowlists in shared provider prompts."""

import json
import unittest

from rail_vehicle.ai.prompts import (
    build_evidence_reference_instructions,
    build_output_contract_instructions,
)
from rail_vehicle.ai.schemas import collect_evidence_ids, collect_evidence_metadata


def input_payload(evidence_id: str, batch_ref: str) -> dict:
    return {
        "schema_version": "railvehicle.ai_input.v1",
        "analysis_type": "single_batch",
        "vehicle": {},
        "selection": {
            "batch_ref": batch_ref,
            "selected_range_utc": {"start": "2026-10-01T00:00:00+00:00", "end": "2026-10-01T00:00:01+00:00"},
        },
        "sample_count": 1,
        "threshold": {},
        "metrics": [{"evidence_id": evidence_id, "name": "测试指标", "value": 1, "unit": "unit", "method": "test"}],
        "threshold_statistics": [],
        "anomaly_events": [],
    }


class EvidencePromptTests(unittest.TestCase):
    def test_allowlist_exactly_matches_current_input_evidence_ids(self):
        payload = input_payload("metric.speed.mean", "B-CURRENT")
        actual_ids = sorted(collect_evidence_ids(payload))
        instructions = build_evidence_reference_instructions(payload)

        self.assertIn(
            "本次唯一允许引用的 evidence_id 清单（直接取自当前结构化上下文）："
            + json.dumps(actual_ids, ensure_ascii=False, separators=(",", ":")),
            instructions,
        )
        self.assertIn("不得创造、猜测、改写", instructions)
        self.assertIn("沿用任何其他请求或历史分析中的 ID", instructions)
        self.assertIn("引用前请逐项核对 ID", instructions)
        self.assertIn("claim_scope.category", instructions)
        self.assertIn("claim_scope.axis", instructions)
        self.assertIn("batch_refs", instructions)
        self.assertIn("不得输出空 evidence_ids", instructions)
        self.assertNotIn("summary 可以没有 evidence", instructions)

    def test_shared_output_contract_matches_required_output_shape(self):
        contract = build_output_contract_instructions()

        for field in (
            "summary",
            "observations",
            "possible_causes",
            "further_checks",
            "limitations",
        ):
            self.assertIn(field, contract)
        self.assertIn('qualification，其值必须逐字为 "hypothesis"', contract)
        self.assertIn("非空 action、非空 reason", contract)
        self.assertIn("至少一个合法 evidence_id", contract)
        self.assertIn("claim_scope", contract)
        self.assertIn("必须包含非 null 的 claim_scope", contract)
        self.assertIn("不得省略 claim_scope", contract)
        self.assertNotIn("claim_scope 可以省略", contract)

    def test_single_batch_metadata_matches_validator_categories_axes_and_batch(self):
        payload = input_payload("metric.speed.mean", "B-SINGLE")
        payload["metrics"].append({
            "evidence_id": "metric.accel.lateral.rms",
            "name": "横向加速度 RMS",
            "value": 0.5,
            "unit": "m/s^2",
            "method": "RMS",
        })
        payload["threshold_statistics"] = [{
            "evidence_id": "metric.exceedance.lateral",
            "name": "横向超阈值",
            "count": 1,
            "percentage": 10,
            "unit": "%",
        }, {
            "evidence_id": "metric.exceedance.either",
            "name": "任一方向超阈值",
            "count": 1,
            "percentage": 10,
            "unit": "%",
        }]
        payload["anomaly_events"] = [{
            "evidence_id": "event.vertical.001",
            "type": "vertical",
            "start_timestamp": "2026-10-01T00:00:00+00:00",
            "end_timestamp": "2026-10-01T00:00:01+00:00",
            "duration_seconds": 1,
            "sample_count": 2,
            "maximum_absolute_value": 1.2,
        }]

        metadata = {
            item["evidence_id"]: item for item in collect_evidence_metadata(payload)
        }
        self.assertEqual(metadata["metric.speed.mean"], {
            "evidence_id": "metric.speed.mean",
            "category": "metric",
            "axis": "not_applicable",
            "batch_refs": ["B-SINGLE"],
        })
        self.assertEqual(metadata["metric.exceedance.lateral"]["category"], "threshold_statistic")
        self.assertEqual(metadata["metric.exceedance.lateral"]["axis"], "lateral")
        self.assertEqual(metadata["metric.exceedance.either"]["axis"], "mixed")
        self.assertEqual(metadata["event.vertical.001"]["category"], "event")
        self.assertEqual(metadata["event.vertical.001"]["axis"], "vertical")
        self.assertEqual(metadata["event.vertical.001"]["batch_refs"], ["B-SINGLE"])

        instructions = build_evidence_reference_instructions(payload)
        metadata_json = json.dumps(
            collect_evidence_metadata(payload), ensure_ascii=False, separators=(",", ":")
        )
        self.assertIn(metadata_json, instructions)
        self.assertIn("引用了不同类别就填 mixed", instructions)
        self.assertIn("没有批次范围时填空数组", instructions)

    def test_comparison_metadata_assigns_each_batch_metric_only_its_own_batch(self):
        payload = {
            "schema_version": "railvehicle.ai_input.v1",
            "analysis_type": "batch_comparison",
            "metrics": [],
            "batches": [
                {
                    "batch_ref": "B1",
                    "metrics": [{
                        "evidence_id": "batch.B1.speed.mean",
                        "name": "B1平均速度",
                        "value": 30,
                        "unit": "km/h",
                        "method": "mean",
                    }, {
                        "evidence_id": "batch.B1.accel.lateral.rms",
                        "name": "B1横向 RMS",
                        "value": 0.4,
                        "unit": "m/s^2",
                        "method": "RMS",
                    }],
                },
                {
                    "batch_ref": "B2",
                    "metrics": [{
                        "evidence_id": "batch.B2.speed.mean",
                        "name": "B2平均速度",
                        "value": 35,
                        "unit": "km/h",
                        "method": "mean",
                    }, {
                        "evidence_id": "batch.B2.accel.vertical.rms",
                        "name": "B2垂向 RMS",
                        "value": 0.6,
                        "unit": "m/s^2",
                        "method": "RMS",
                    }],
                },
            ],
        }

        metadata = {
            item["evidence_id"]: item for item in collect_evidence_metadata(payload)
        }
        self.assertEqual(metadata["batch.B1.speed.mean"]["category"], "batch_metric")
        self.assertEqual(metadata["batch.B1.speed.mean"]["axis"], "not_applicable")
        self.assertEqual(metadata["batch.B1.speed.mean"]["batch_refs"], ["B1"])
        self.assertEqual(metadata["batch.B1.accel.lateral.rms"]["axis"], "lateral")
        self.assertEqual(metadata["batch.B1.accel.lateral.rms"]["batch_refs"], ["B1"])
        self.assertEqual(metadata["batch.B2.speed.mean"]["batch_refs"], ["B2"])
        self.assertEqual(metadata["batch.B2.accel.vertical.rms"]["axis"], "vertical")
        self.assertEqual(metadata["batch.B2.accel.vertical.rms"]["batch_refs"], ["B2"])

    def test_same_evidence_id_metadata_is_rebuilt_from_each_request(self):
        first = input_payload("metric.speed.mean", "B-FIRST")
        second = input_payload("metric.speed.mean", "B-SECOND")
        second["metrics"] = []
        second["threshold_statistics"] = [{
            "evidence_id": "metric.speed.mean",
            "name": "阈值样例",
            "count": 1,
            "percentage": 100,
            "unit": "%",
        }]

        first_prompt = build_evidence_reference_instructions(first)
        second_prompt = build_evidence_reference_instructions(second)
        first_metadata = json.dumps(
            collect_evidence_metadata(first), ensure_ascii=False, separators=(",", ":")
        )
        second_metadata = json.dumps(
            collect_evidence_metadata(second), ensure_ascii=False, separators=(",", ":")
        )

        self.assertIn(first_metadata, first_prompt)
        self.assertNotIn(first_metadata, second_prompt)
        self.assertIn(second_metadata, second_prompt)
        self.assertIn('"category":"metric"', first_metadata)
        self.assertIn('"category":"threshold_statistic"', second_metadata)
        self.assertIn('"batch_refs":["B-FIRST"]', first_metadata)
        self.assertIn('"batch_refs":["B-SECOND"]', second_metadata)

    def test_allowlist_is_rebuilt_for_each_request_without_stale_ids(self):
        first = input_payload("metric.first_request.value", "B-FIRST")
        second = input_payload("metric.second_request.value", "B-SECOND")

        first_instructions = build_evidence_reference_instructions(first)
        second_instructions = build_evidence_reference_instructions(second)

        self.assertIn("metric.first_request.value", first_instructions)
        self.assertNotIn("metric.first_request.value", second_instructions)
        self.assertIn("metric.second_request.value", second_instructions)
        self.assertNotIn("metric.second_request.value", first_instructions)
        self.assertNotIn("B-FIRST", second_instructions)
        self.assertNotIn("B-SECOND", first_instructions)


if __name__ == "__main__":
    unittest.main()
