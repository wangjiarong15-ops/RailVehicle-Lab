"""Strict JSON contracts for AI requests and validated responses."""

from __future__ import annotations

import json
import math
from typing import Any

from jsonschema import Draft202012Validator


class AIContractError(ValueError):
    """Raised when an AI payload or response violates the public contract."""


class AIOutputParseError(AIContractError):
    """Raised when provider output cannot be decoded as a JSON document."""


INPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "RailVehicle AI analysis input",
    "type": "object",
    "required": ["schema_version", "analysis_type", "metrics"],
    "properties": {
        "schema_version": {"const": "railvehicle.ai_input.v1"},
        "analysis_type": {"enum": ["single_batch", "batch_comparison"]},
        "vehicle": {
            "type": "object",
            "properties": {
                "vehicle_type": {"type": "string"},
                "mass_kg": {"type": ["number", "null"]},
                "axle_count": {"type": ["integer", "null"]},
                "bogie_count": {"type": ["integer", "null"]},
                "max_speed_kmh": {"type": ["number", "null"]},
            },
            "additionalProperties": False,
        },
        "selection": {
            "type": "object",
            "required": ["batch_ref", "selected_range_utc"],
            "properties": {
                "batch_ref": {"type": "string"},
                "selected_range_utc": {
                    "type": "object",
                    "required": ["start", "end"],
                    "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "sample_count": {"type": "integer", "minimum": 1},
        "threshold": {"type": "object"},
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["evidence_id", "name", "value", "unit", "method"],
                "properties": {
                    "evidence_id": {"type": "string", "minLength": 1},
                    "name": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                    "method": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "threshold_statistics": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["evidence_id", "name", "count", "percentage", "unit"],
                "properties": {
                    "evidence_id": {"type": "string", "minLength": 1},
                    "name": {"type": "string"},
                    "count": {"type": "integer", "minimum": 0},
                    "percentage": {"type": "number", "minimum": 0, "maximum": 100},
                    "unit": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "anomaly_events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "evidence_id", "type", "start_timestamp", "end_timestamp",
                    "duration_seconds", "sample_count", "maximum_absolute_value",
                ],
                "properties": {
                    "evidence_id": {"type": "string", "minLength": 1},
                    "type": {"enum": ["lateral", "vertical"]},
                    "start_timestamp": {"type": "string"},
                    "end_timestamp": {"type": "string"},
                    "duration_seconds": {"type": "number", "minimum": 0},
                    "sample_count": {"type": "integer", "minimum": 1},
                    "maximum_absolute_value": {"type": "number", "minimum": 0},
                },
                "additionalProperties": False,
            },
        },
        "batches": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "required": ["batch_ref", "metrics"],
                "properties": {
                    "batch_ref": {"type": "string"},
                    "metrics": {"type": "array", "items": {"$ref": "#/$defs/metric"}},
                },
                "additionalProperties": False,
            },
        },
    },
    "$defs": {
        "metric": {
            "type": "object",
            "required": ["evidence_id", "name", "value", "unit", "method"],
            "properties": {
                "evidence_id": {"type": "string", "minLength": 1},
                "name": {"type": "string"},
                "value": {"type": "number"},
                "unit": {"type": "string"},
                "method": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "allOf": [
        {
            "if": {"properties": {"analysis_type": {"const": "single_batch"}}},
            "then": {
                "required": ["vehicle", "selection", "sample_count", "threshold", "threshold_statistics", "anomaly_events"]
            },
        },
        {
            "if": {"properties": {"analysis_type": {"const": "batch_comparison"}}},
            "then": {"required": ["batches"]},
        },
    ],
    "additionalProperties": False,
}

_CLAIM = {
    "type": "object",
    "required": ["text", "evidence_ids"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
    },
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "RailVehicle AI analysis output",
    "type": "object",
    "required": ["summary", "observations", "possible_causes", "further_checks", "limitations"],
    "properties": {
        "summary": _CLAIM,
        "observations": {"type": "array", "items": _CLAIM},
        "possible_causes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "evidence_ids", "qualification"],
                "properties": {
                    "text": {"type": "string", "minLength": 1},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "qualification": {"const": "hypothesis"},
                },
                "additionalProperties": False,
            },
        },
        "further_checks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action", "reason", "evidence_ids"],
                "properties": {
                    "action": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                },
                "additionalProperties": False,
            },
        },
        "limitations": {"type": "array", "items": _CLAIM},
    },
    "additionalProperties": False,
}


def _validate(schema: dict[str, Any], value: Any, label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda err: list(map(str, err.path)))
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.absolute_path)) or "根对象"
        raise AIContractError(f"{label}不符合 Schema（{location}）：{error.message}")


def validate_input_payload(payload: Any) -> dict[str, Any]:
    _validate(INPUT_SCHEMA, payload, "AI 输入")
    return payload


def collect_evidence_ids(payload: dict[str, Any]) -> set[str]:
    """Collect only identifiers carried by a valid, sanitized input payload."""
    validate_input_payload(payload)
    evidence: set[str] = set()
    for field in ("metrics", "threshold_statistics", "anomaly_events"):
        evidence.update(item["evidence_id"] for item in payload.get(field, []))
    for batch in payload.get("batches", []):
        evidence.update(item["evidence_id"] for item in batch.get("metrics", []))
    return evidence


def validate_output(raw: str | bytes | dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Parse and validate provider output, including all traceable evidence refs."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise AIOutputParseError("AI 输出不是有效 UTF-8 文本。") from None
    if isinstance(raw, str):
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Avoid echoing provider output or snippets into UI errors/logs.
            raise AIOutputParseError("AI 输出不是有效 JSON。") from None
    elif isinstance(raw, dict):
        result = raw
    else:
        raise AIOutputParseError("AI 输出必须是 JSON 字符串或对象。")
    if not _contains_only_finite_numbers(result):
        raise AIContractError("AI 输出包含 NaN 或无穷大等非有限数值。")
    _validate(OUTPUT_SCHEMA, result, "AI 输出")
    text = " ".join(
        value
        for claim in _iter_claims(result)
        for value in claim.values()
        if isinstance(value, str)
    )
    forbidden_claims = ("车辆故障", "发生故障", "安全事故", "不安全", "符合标准", "不符合标准")
    if any(term in text for term in forbidden_claims):
        raise AIContractError("AI 输出包含未经允许的故障、安全或合规判断措辞。")
    known = collect_evidence_ids(payload)
    cited = {evidence_id for claim in _iter_claims(result) for evidence_id in claim["evidence_ids"]}
    unknown = sorted(cited - known)
    if unknown:
        raise AIContractError(f"AI 输出引用了不存在的 evidence_id：{', '.join(unknown)}")
    return result


def _contains_only_finite_numbers(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_contains_only_finite_numbers(item) for item in value.values())
    if isinstance(value, list):
        return all(_contains_only_finite_numbers(item) for item in value)
    return True


def _iter_claims(result: dict[str, Any]):
    yield result["summary"]
    for key in ("observations", "possible_causes", "further_checks", "limitations"):
        yield from result[key]
