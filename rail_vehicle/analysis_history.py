"""Local single-batch analysis history and source declarations."""

from datetime import datetime, timezone
from hashlib import sha256
import json

from rail_vehicle import db
from rail_vehicle.replay_evidence import ReplayEvidenceScope


SOURCES = {"unknown": "来源未确认", "measurement": "真实测量（用户声明）",
           "test": "测试数据（用户声明）", "synthetic": "合成数据"}
RULE_VERSION = "per-axis-strict-absolute-v1"


def source_kind(declared, engineering_json=None):
    if declared in SOURCES and declared != "unknown":
        return declared
    try:
        kind = json.loads(engineering_json)["manifest"]["source_kind"]
    except (KeyError, TypeError, ValueError):
        return "unknown"
    return {"field_declared": "measurement", "synthetic": "synthetic"}.get(kind, "unknown") if isinstance(kind, str) else "unknown"


def threshold_label(value):
    return "预置演示阈值（1 m/s²）" if value == 1 else "用户自定义阈值"


def record_batch_analysis(vehicle, batch, source_digest, source, threshold, scope, db_path=None):
    payload = [vehicle, batch, source_digest, source, threshold, scope.first_row, scope.last_row,
               scope.batch_count, scope.first_timestamp.isoformat(), scope.last_timestamp.isoformat(), RULE_VERSION]
    fingerprint = sha256(json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with db.get_connection(db_path if db_path is not None else db.DB_PATH) as con:
        con.execute("""INSERT INTO run_analysis_history
            (fingerprint, vehicle_id, batch_id, source_sha256, source_kind, threshold,
             first_row, last_row, batch_count, start_utc, end_utc, rule_version, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET analyzed_at = excluded.analyzed_at""",
                    (fingerprint, *payload, datetime.now(timezone.utc).isoformat(timespec="seconds")))


def recent_batch_analyses(con):
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_analysis_history'").fetchone()
    if not exists:
        return []
    rows = con.execute("""SELECT h.*, v.vehicle_code
        FROM run_analysis_history h JOIN vehicles v ON h.vehicle_id = v.id
        JOIN import_batches b ON h.batch_id = b.id AND h.source_sha256 IS b.source_sha256
        ORDER BY h.analyzed_at DESC, h.id DESC LIMIT 8""")
    return [dict(row) for row in rows]


def saved_scope(record):
    return ReplayEvidenceScope(record["first_row"], record["last_row"], record["batch_count"],
                               datetime.fromisoformat(record["start_utc"]), datetime.fromisoformat(record["end_utc"]))
