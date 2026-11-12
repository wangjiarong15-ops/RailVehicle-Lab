"""Local snapshots of explicitly saved, evidence-validated interpretations."""

import json
from datetime import datetime, timezone
from math import isfinite
from re import fullmatch

from rail_vehicle import db
from rail_vehicle.ai.provider import input_fingerprint
from rail_vehicle.ai.schemas import validate_output
from rail_vehicle.ai.ui import selection_fingerprint
from rail_vehicle.replay_evidence import valid_row_span
from rail_vehicle.timestamp_utils import parse_iso_timestamp


PAGE_SIZE = 20
_SELECTION_FIELDS = frozenset({"vehicle_id", "batch_id", "selected_start", "selected_end", "threshold"})
_EXPERIMENT_FIELDS = {
    "ml_experiment": {"source", "vehicle", "reference", "target"},
    "vibration_experiment": {"source", "vehicle", "batch"},
    "fleet_comparison": {"source", "entries"},
}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _valid_id(value):
    return type(value) is int and value > 0


def _matches_batch_refs(payload, count):
    return [batch["batch_ref"] for batch in payload["batches"]] == [f"B{i}" for i in range(1, count + 1)]


def _scope(payload, selection):
    """Allow only structured local IDs and selection parameters in the archive."""
    kind = payload["analysis_type"]
    if kind == "single_batch":
        if set(selection) not in (_SELECTION_FIELDS, _SELECTION_FIELDS | {"row_span"}):
            raise ValueError("单批次范围不完整。")
        if "row_span" in selection and not valid_row_span(selection["row_span"], payload["sample_count"]):
            raise ValueError("回放窗口的样本行与统计数量不一致。")
        if not all(_valid_id(selection[name]) for name in ("vehicle_id", "batch_id")):
            raise ValueError("历史记录缺少有效车辆或批次。")
        selected_range = payload["selection"]["selected_range_utc"]
        threshold = selection["threshold"]
        if (type(threshold) not in (int, float) or not isfinite(threshold) or threshold < 0
                or threshold != payload["threshold"]["value"]
                or selection["selected_start"] != selected_range["start"]
                or selection["selected_end"] != selected_range["end"]
                or parse_iso_timestamp(selected_range["start"]) > parse_iso_timestamp(selected_range["end"])):
            raise ValueError("保存范围与证据不一致。")
        return kind, "history", selection["vehicle_id"], selection["batch_id"]
    if kind == "batch_comparison":
        if set(selection) != {"vehicle_id", "batch_ids"} or not _valid_id(selection["vehicle_id"]):
            raise ValueError("同车对比范围不完整。")
        batches = selection["batch_ids"]
        if (not isinstance(batches, (list, tuple)) or len(batches) < 2 or not all(map(_valid_id, batches))
                or len(set(batches)) != len(batches) or not _matches_batch_refs(payload, len(batches))):
            raise ValueError("对比批次与证据不一致。")
        return kind, "history", selection["vehicle_id"], 0
    if set(selection) != {"selection", "analysis_fingerprint"} or not fullmatch(r"[a-f0-9]{64}", selection["analysis_fingerprint"]):
        raise ValueError("实验范围不完整。")
    local = selection["selection"]
    source = local["source"]
    if source == "demo" and set(local) == {"source"}:
        return kind, source, 0, 0
    if source != "history" or set(local) != _EXPERIMENT_FIELDS[kind]:
        raise ValueError("无法确认实验数据来源。")
    if kind == "fleet_comparison":
        entries = local["entries"]
        if (not isinstance(entries, list) or not 2 <= len(entries) <= 6
                or not all(isinstance(entry, dict) and set(entry) == {"vehicle_id", "batch_id"}
                           and all(map(_valid_id, entry.values())) for entry in entries)
                or len({entry["vehicle_id"] for entry in entries}) != len(entries)
                or not _matches_batch_refs(payload, len(entries))):
            raise ValueError("多车辆批次与证据不一致。")
        return kind, source, 0, 0
    if not all(_valid_id(local[name]) for name in set(local) - {"source"}):
        raise ValueError("实验车辆或批次无效。")
    if kind == "ml_experiment" and (local["reference"] == local["target"] or not _matches_batch_refs(payload, 2)):
        raise ValueError("机器学习实验的参考批次和检测批次不一致。")
    # Zero is only an internal placeholder for records spanning multiple vehicles or batches.
    return kind, source, local["vehicle"], local.get("batch", 0)


def _snapshot(payload, selection, result):
    evidence_hash = input_fingerprint(payload)
    scope = _scope(payload, selection)
    scope_hash = selection_fingerprint(selection)
    if (result.get("status") != "available" or result.get("scope_validation") != "verified"
            or result.get("input_fingerprint") != evidence_hash
            or result.get("selection_fingerprint") != scope_hash):
        raise ValueError("AI 解读与统计证据不一致。")
    return scope, evidence_hash, scope_hash, validate_output(result["analysis"], payload)


def save_history(payload, selection, result, *, db_path=None):
    """Return whether a new record was written. Re-saving the same run is a no-op."""
    (kind, source, vehicle_id, batch_id), evidence_hash, scope_hash, analysis = _snapshot(payload, selection, result)
    metadata = result["ai_metadata"]
    provider = metadata["provider"]
    generated_at = datetime.fromisoformat(metadata["generated_at"])
    if provider not in ("openai", "deepseek") or generated_at.tzinfo is None:
        raise ValueError("AI 生成记录缺少有效来源或时间。")
    with db.get_connection(db.DB_PATH if db_path is None else db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO ai_analysis_history
               (vehicle_id, batch_id, generated_at, provider, analysis_type, data_source, input_fingerprint,
                selection_fingerprint, selection_json, evidence_json, analysis_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(generated_at, input_fingerprint, selection_fingerprint) DO NOTHING""",
            (vehicle_id, batch_id, generated_at.astimezone(timezone.utc).isoformat(), provider, kind, source,
             evidence_hash, scope_hash, _encode(selection), _encode(payload), _encode(analysis)),
        )
        return cursor.rowcount == 1


def list_history(page, *, analysis_type=None, db_path=None):
    """Read only list metadata; load evidence for the selected entry separately."""
    where = "" if analysis_type is None else " WHERE analysis_type = ?"
    parameters = () if analysis_type is None else (analysis_type,)
    with db.get_connection(db.DB_PATH if db_path is None else db_path) as connection:
        connection.execute("BEGIN")
        count = connection.execute("SELECT COUNT(*) FROM ai_analysis_history" + where, parameters).fetchone()[0]
        last = max(0, (count - 1) // PAGE_SIZE)
        page = min(max(page, 0), last)
        rows = connection.execute(
            "SELECT id, vehicle_id, batch_id, generated_at, provider, analysis_type, data_source FROM ai_analysis_history"
            + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*parameters, PAGE_SIZE, page * PAGE_SIZE),
        ).fetchall()
    return [dict(row) for row in rows], page, last


def load_history(record_id, *, db_path=None):
    with db.get_connection(db.DB_PATH if db_path is None else db_path) as connection:
        row = connection.execute("SELECT * FROM ai_analysis_history WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        return None
    payload, selection, analysis = (json.loads(row[name]) for name in ("evidence_json", "selection_json", "analysis_json"))
    result = dict(status="available", scope_validation="verified", analysis=analysis,
                  input_fingerprint=row["input_fingerprint"], selection_fingerprint=row["selection_fingerprint"])
    scope, _, _, result["analysis"] = _snapshot(payload, selection, result)
    if scope != (row["analysis_type"], row["data_source"], row["vehicle_id"], row["batch_id"]):
        raise ValueError("历史记录与保存范围不一致。")
    return payload, selection, result


def delete_history(record_id, *, db_path=None):
    with db.get_connection(db.DB_PATH if db_path is None else db_path) as connection:
        connection.execute("DELETE FROM ai_analysis_history WHERE id = ?", (record_id,))
