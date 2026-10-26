"""Strict JSON contracts for AI requests and validated responses."""

from __future__ import annotations

import json
import math
from typing import Any

from jsonschema import Draft202012Validator


AI_OUTPUT_ERROR_CODES = frozenset({
    "output_json_invalid",
    "claim_scope_schema_invalid",
    "evidence_id_unknown",
    "evidence_scope_category_mismatch",
    "evidence_scope_axis_mismatch",
    "evidence_scope_batch_mismatch",
    "output_schema_invalid",
    "output_non_finite_number",
    "unsafe_claim_text",
    "output_contract_invalid",
    "input_duplicate_evidence_id",
})

_SAFE_ERROR_MESSAGES = {
    "output_json_invalid": "AI 返回文本不是有效 JSON，未展示结果。",
    "claim_scope_schema_invalid": "AI 输出的 claim_scope 不符合 Schema。",
    "evidence_id_unknown": "AI 输出引用了不存在的 evidence_id。",
    "evidence_scope_category_mismatch": "AI 输出 claim_scope 的 category 与引用证据不匹配。",
    "evidence_scope_axis_mismatch": "AI 输出 claim_scope 的 axis 与引用证据不匹配。",
    "evidence_scope_batch_mismatch": "AI 输出 claim_scope 的 batch_refs 与引用证据不匹配。",
    "output_schema_invalid": "AI 输出不符合所需 Schema。",
    "output_non_finite_number": "AI 输出包含 NaN 或无穷大等非有限数值。",
    "unsafe_claim_text": "AI 输出包含未经允许的故障、安全或合规判断措辞。",
    "output_contract_invalid": "AI 输出未通过契约校验。",
    "input_duplicate_evidence_id": "AI 输入包含重复 evidence_id，已拒绝。",
}


class AIContractError(ValueError):
    """Raised when an AI payload or response violates the public contract."""

    def __init__(self, message: str = "", *, code: str = "output_contract_invalid"):
        # Discard provider/validator details: they can contain model output.
        self.code = code if isinstance(code, str) and code in AI_OUTPUT_ERROR_CODES else "output_contract_invalid"
        super().__init__(_SAFE_ERROR_MESSAGES[self.code])


class AIOutputParseError(AIContractError):
    """Raised when provider output cannot be decoded as a JSON document."""

    def __init__(self, message: str = ""):
        super().__init__(message, code="output_json_invalid")


INPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "RailVehicle AI analysis input",
    "type": "object",
    "required": ["schema_version", "analysis_type", "metrics"],
    "properties": {
        "schema_version": {"const": "railvehicle.ai_input.v1"},
        "analysis_type": {"enum": ["single_batch", "batch_comparison", "ml_experiment", "vibration_experiment", "fleet_comparison"]},
        "comparison": {
            "type": "object", "required": ["method", "limitations"],
            "properties": {"method": {"type": "string", "minLength": 1},
                           "limitations": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            "additionalProperties": False,
        },
        "experiment": {
            "type": "object",
            "required": ["method", "ranges", "limitations"],
            "properties": {
                "method": {"type": "string", "minLength": 1},
                "ranges": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "required": ["batch_ref", "start", "end"],
                    "properties": {"batch_ref": {"type": "string", "minLength": 1},
                                   "start": {"type": "string"}, "end": {"type": "string"}},
                    "additionalProperties": False,
                }},
                "limitations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
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
            "if": {"properties": {"analysis_type": {"const": "fleet_comparison"}}},
            "then": {"required": ["batches", "comparison"]},
        },
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
        {
            "if": {"properties": {"analysis_type": {"const": "ml_experiment"}}},
            "then": {"required": ["experiment", "sample_count", "batches"]},
        },
        {
            "if": {"properties": {"analysis_type": {"const": "vibration_experiment"}}},
            "then": {"required": ["experiment", "sample_count", "selection"]},
        },
    ],
    "additionalProperties": False,
}

CLAIM_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["category", "axis", "batch_refs"],
    "properties": {
        "category": {"enum": ["metric", "threshold_statistic", "event", "batch_metric", "mixed"]},
        "axis": {"enum": ["lateral", "vertical", "longitudinal", "not_applicable", "mixed"]},
        "batch_refs": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "additionalProperties": False,
}
# Kept optional at the JSON Schema layer solely so stored legacy output can be
# parsed when an explicit compatibility flag is supplied. New output is made
# strict by validate_output() and by both providers' request schemas.
_OPTIONAL_CLAIM_SCOPE_SCHEMA = {**CLAIM_SCOPE_SCHEMA, "type": ["object", "null"]}

_CLAIM = {
    "type": "object",
    "required": ["text", "evidence_ids"],
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "claim_scope": _OPTIONAL_CLAIM_SCOPE_SCHEMA,
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
                    "claim_scope": _OPTIONAL_CLAIM_SCOPE_SCHEMA,
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
                    "claim_scope": _OPTIONAL_CLAIM_SCOPE_SCHEMA,
                },
                "additionalProperties": False,
            },
        },
        "limitations": {"type": "array", "items": _CLAIM},
    },
    "additionalProperties": False,
}


def _validate(
    schema: dict[str, Any],
    value: Any,
    label: str,
    *,
    error_code: str = "output_contract_invalid",
    claim_scope_error_code: str | None = None,
) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda err: list(map(str, err.path)))
    if errors:
        error = errors[0]
        code = error_code
        if claim_scope_error_code and "claim_scope" in error.absolute_path:
            code = claim_scope_error_code
        raise AIContractError(f"{label}不符合 Schema。", code=code)


def validate_input_payload(payload: Any) -> dict[str, Any]:
    _validate(INPUT_SCHEMA, payload, "AI 输入")
    evidence_ids = _input_evidence_ids(payload)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise AIContractError("AI 输入包含重复 evidence_id，已拒绝。", code="input_duplicate_evidence_id")
    return payload


def _input_evidence_ids(payload: dict[str, Any]) -> list[str]:
    evidence_ids = [item["evidence_id"] for item in payload.get("metrics", [])]
    evidence_ids.extend(item["evidence_id"] for item in payload.get("threshold_statistics", []))
    evidence_ids.extend(item["evidence_id"] for item in payload.get("anomaly_events", []))
    for batch in payload.get("batches", []):
        evidence_ids.extend(item["evidence_id"] for item in batch.get("metrics", []))
    return evidence_ids


def _evidence_catalog(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Derive typed evidence metadata from existing input structure and IDs."""
    catalog: dict[str, dict[str, Any]] = {}
    selected_batch = payload.get("selection", {}).get("batch_ref")

    def add(evidence_id: str, category: str, batch_ref: str | None, axis: str | None = None) -> None:
        if axis is None:
            parts = evidence_id.split(".")
            axes = {part for part in parts if part in {"lateral", "vertical", "longitudinal"}}
            if len(axes) == 1:
                axis = axes.pop()
            elif len(axes) > 1 or "either" in parts:
                axis = "mixed"
            else:
                axis = "not_applicable"
        catalog[evidence_id] = {
            "category": category,
            "axis": axis,
            "batch_ref": batch_ref,
        }

    for item in payload.get("metrics", []):
        add(item["evidence_id"], "metric", selected_batch)
    for item in payload.get("threshold_statistics", []):
        add(item["evidence_id"], "threshold_statistic", selected_batch)
    for item in payload.get("anomaly_events", []):
        add(item["evidence_id"], "event", selected_batch, item["type"])
    for batch in payload.get("batches", []):
        for item in batch.get("metrics", []):
            add(item["evidence_id"], "batch_metric", batch["batch_ref"])
    return catalog


def collect_evidence_metadata(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose the validator's evidence mapping for request-local prompt guidance."""
    validate_input_payload(payload)
    catalog = _evidence_catalog(payload)
    return [
        {
            "evidence_id": evidence_id,
            "category": evidence["category"],
            "axis": evidence["axis"],
            "batch_refs": (
                [evidence["batch_ref"]]
                if evidence["batch_ref"] is not None
                else []
            ),
        }
        for evidence_id, evidence in sorted(catalog.items())
    ]


def collect_evidence_ids(payload: dict[str, Any]) -> set[str]:
    """Collect only identifiers carried by a valid, sanitized input payload."""
    validate_input_payload(payload)
    return set(_input_evidence_ids(payload))


def validate_output(
    raw: str | bytes | dict[str, Any],
    payload: dict[str, Any],
    *,
    allow_legacy_scope: bool = False,
) -> dict[str, Any]:
    """Parse and validate provider output, including all traceable evidence refs.

    New AI output must include a non-null claim_scope for every claim. Callers
    may opt into the legacy path only when handling an already-stored result;
    evidence existence and safety checks still run, but missing scopes are not
    represented as fully range-verified.
    """
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
        raise AIContractError("AI 输出包含 NaN 或无穷大等非有限数值。", code="output_non_finite_number")
    if not isinstance(result, dict):
        raise AIContractError("AI 输出根节点必须是 JSON 对象。", code="output_schema_invalid")
    _validate(
        OUTPUT_SCHEMA,
        result,
        "AI 输出",
        error_code="output_schema_invalid",
        claim_scope_error_code="claim_scope_schema_invalid",
    )
    text = " ".join(
        value
        for claim in _iter_claims(result)
        for value in claim.values()
        if isinstance(value, str)
    )
    forbidden_claims = ("车辆故障", "发生故障", "安全事故", "不安全", "符合标准", "不符合标准")
    if any(term in text for term in forbidden_claims):
        raise AIContractError("AI 输出包含未经允许的故障、安全或合规判断措辞。", code="unsafe_claim_text")
    known = collect_evidence_ids(payload)
    cited = {evidence_id for claim in _iter_claims(result) for evidence_id in claim["evidence_ids"]}
    unknown = sorted(cited - known)
    if unknown:
        raise AIContractError("AI 输出引用了不存在的 evidence_id。", code="evidence_id_unknown")
    catalog = _evidence_catalog(payload)
    for claim in _iter_claims(result):
        declared_scope = claim.get("claim_scope")
        if declared_scope is None:
            if not allow_legacy_scope:
                raise AIContractError(
                    "AI 输出缺少 claim_scope，无法验证证据范围。",
                    code="claim_scope_schema_invalid",
                )
            continue
        _validate_claim_scope(claim["evidence_ids"], declared_scope, catalog)
    return result


def has_unverified_claim_scopes(output: dict[str, Any]) -> bool:
    """Whether a validated legacy-compatible output has any absent/null scope."""
    return any(claim.get("claim_scope") is None for claim in _iter_claims(output))


def _validate_claim_scope(
    evidence_ids: list[str], declared: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> None:
    if len(declared["batch_refs"]) != len(set(declared["batch_refs"])):
        raise AIContractError("AI 输出 claim_scope 的 batch_refs 重复。", code="evidence_scope_batch_mismatch")
    referenced = [catalog[evidence_id] for evidence_id in evidence_ids]
    categories = {item["category"] for item in referenced}
    expected_category = next(iter(categories)) if len(categories) == 1 else "mixed"
    if declared["category"] != expected_category:
        raise AIContractError(
            "AI 输出 claim_scope 的 category 与引用证据不匹配。",
            code="evidence_scope_category_mismatch",
        )

    axes = {item["axis"] for item in referenced if item["axis"] != "not_applicable"}
    expected_axis = next(iter(axes)) if len(axes) == 1 else "mixed" if axes else "not_applicable"
    if declared["axis"] != expected_axis:
        raise AIContractError("AI 输出 claim_scope 的 axis 与引用证据不匹配。", code="evidence_scope_axis_mismatch")

    expected_batches = {item["batch_ref"] for item in referenced if item["batch_ref"] is not None}
    if set(declared["batch_refs"]) != expected_batches:
        raise AIContractError("AI 输出 claim_scope 的 batch_refs 与引用证据不匹配。", code="evidence_scope_batch_mismatch")


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
