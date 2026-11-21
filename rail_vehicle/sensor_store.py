"""Append-only sensor configuration scoped to the (vehicle, batch, CSV channel)."""

import json
import re
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path

from rail_vehicle import db
from rail_vehicle.sensor_mapping import CHANNELS, validate_metadata


@contextmanager
def _read(db_path):
    uri = Path(db_path or db.DB_PATH).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as con:
        con.row_factory = sqlite3.Row
        yield con


def _records(connection, vehicle_id, batch_id, channel=None):
    sql = """SELECT channel, version, serial, metadata, changed_at FROM sensor_revisions
             WHERE vehicle_id=? AND batch_id=?"""
    params = [vehicle_id, batch_id]
    if channel is not None:
        sql += " AND channel=?"
        params.append(channel)
    else:
        sql += """ AND NOT EXISTS (SELECT 1 FROM sensor_revisions newer
                   WHERE newer.batch_id=sensor_revisions.batch_id AND newer.vehicle_id=sensor_revisions.vehicle_id
                   AND newer.channel=sensor_revisions.channel AND newer.version>sensor_revisions.version)"""
    rows = connection.execute(sql+" ORDER BY channel,version DESC", params)
    return [dict(**{k: row[k] for k in ("channel", "version", "serial", "changed_at")},
                 metadata=json.loads(row["metadata"]) if row["metadata"] is not None else None) for row in rows]


def sensor_snapshot(vehicle_id, batch_id, db_path=None):
    with _read(db_path) as con:
        con.execute("BEGIN")
        batch = con.execute("""SELECT b.file_name,b.source_sha256,b.engineering_json,v.vehicle_code FROM import_batches b, vehicles v
            WHERE b.id=? AND v.id=? AND EXISTS (SELECT 1 FROM run_samples s
            WHERE s.batch_id=b.id AND s.vehicle_id=v.id)""", (batch_id, vehicle_id)).fetchone()
        if batch is None:
            return {}
        fields = dict(batch)
        intake = fields.pop("engineering_json")
        return dict(batch_id=batch_id, vehicle_id=vehicle_id, **fields, engineering=json.loads(intake) if intake else None,
                    channels={r["channel"]: r for r in _records(con, vehicle_id, batch_id)})


def revisions(vehicle_id, batch_id, channel, db_path=None):
    with _read(db_path) as con:
        return _records(con, vehicle_id, batch_id, channel)


def revise(vehicle_id, batch_id, channel, serial, metadata, *, expected_version, db_path=None):
    """Use metadata=None to withdraw the binding. Previous versions remain addressable."""
    if channel not in CHANNELS or type(expected_version) is not int or expected_version < 0:
        raise ValueError("通道或配置版本无效。")
    if not isinstance(serial, str) or not re.fullmatch(r"[\w.-]{1,64}", serial):
        raise ValueError("传感器编号限 1 至 64 位字母、数字、下划线、点或短横线。")
    if metadata is not None:
        metadata = validate_metadata(metadata)
    with db.get_connection(db_path or db.DB_PATH) as con:
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM run_samples WHERE vehicle_id=? AND batch_id=? LIMIT 1",
                           (vehicle_id, batch_id)).fetchone():
            raise ValueError("车辆与批次不匹配。")
        current = {r["channel"]: r for r in _records(con, vehicle_id, batch_id)}
        old = current.get(channel, {})
        if old.get("version", 0) != expected_version:
            raise ValueError("配置已在其他会话更新，请重新打开最新版本。")
        if metadata is None and (old.get("metadata") is None or old["serial"] != serial):
            raise ValueError("当前没有该传感器的有效绑定。")
        owner = con.execute("SELECT vehicle_id FROM sensor_devices WHERE serial=?", (serial,)).fetchone()
        if owner and owner[0] != vehicle_id:
            raise ValueError("传感器编号已归属另一辆车。")
        if metadata and metadata.get("axis") and any(
            k != channel and r["serial"] == serial and (r["metadata"] or {}).get("axis","")[-1:] == metadata["axis"][-1]
            for k, r in current.items()
        ):
            raise ValueError("该传感器的同一测量轴已关联本批次另一通道。")
        if not owner:
            con.execute("INSERT INTO sensor_devices(serial,vehicle_id) VALUES(?,?)", (serial, vehicle_id))
        version = expected_version + 1
        payload = None if metadata is None else json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        con.execute("""INSERT INTO sensor_revisions(batch_id,vehicle_id,channel,version,serial,metadata)
                       VALUES(?,?,?,?,?,?)""", (batch_id, vehicle_id, channel, version, serial, payload))
        return version


def active_channels(snapshot):
    return {k: r for k, r in snapshot.get("channels", {}).items() if r["metadata"] is not None}


def channel_inventory(snapshot):
    rows = []
    for channel, rec in active_channels(snapshot).items():
        m = rec["metadata"]
        rows.append({"通道": channel, "传感器": rec["serial"], "版本": rec["version"],
                     "部件 ID": m.get("part_id"), "来源凭据": m.get("source"),
                     "配置性质": {"illustration": "教学示意", "recorded": "现场记录"}.get(m.get("origin"), "未知"),
                     "位置": STATE_NAMES[m.get("mount_status", "unknown")],
                     "测量轴": m.get("axis"), "标定": STATE_NAMES[m.get("calibration", "unknown")],
                     "单位": m.get("unit"), "采样率 Hz": m.get("rate_hz"), "时间基准": m.get("time_basis"),
                     "时钟核验": STATE_NAMES[m.get("clock_status", "unknown")]})
    return rows


STATE_NAMES = {"unknown": "未知", "unverified": "待核验", "verified": "已核验"}
