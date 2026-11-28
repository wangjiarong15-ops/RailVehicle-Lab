"""Append-only SQLite checkpoints for bounded device capture and recovery."""

from dataclasses import asdict
from hashlib import sha256
import json
from uuid import uuid4

from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.measurement_stream import MAX_SAMPLES, Packet, read_declaration
from rail_vehicle.virtual_trials import json_text


def _chain(previous, text):
    return sha256(previous.encode() + b"\n" + text.encode()).hexdigest()


def open_recording(label, acquisition, db_path=DB_PATH):
    label = label.strip()
    if not 1 <= len(label) <= 80:
        raise ValueError("采集记录名称须为 1 至 80 个字符。")
    identifier = uuid4().hex
    declaration = json_text(acquisition.declaration())
    with get_connection(db_path) as db:
        db.execute("INSERT INTO acquisition_sessions(id,label,declaration,digest) VALUES (?,?,?,?)",
                   (identifier, label, declaration, _chain("", declaration)))
    return identifier


def append_packets(identifier, offset, acquisition, db_path=DB_PATH):
    count = len(acquisition.packets)
    if type(offset) is not int or not 0 <= offset <= count <= MAX_SAMPLES * 3:
        raise ValueError("采集缓冲位置无效。")
    with get_connection(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        header = db.execute("SELECT declaration,packet_count,digest FROM acquisition_sessions WHERE id=?",
                            (identifier,)).fetchone()
        if not header or header["packet_count"] != offset or header["declaration"] != json_text(acquisition.declaration()):
            raise ValueError("记录已移动或设备声明发生变化，请重新读取保存位置。")
        if offset:
            previous = db.execute("SELECT packet FROM acquisition_packets WHERE session_id=? AND offset=?",
                                  (identifier, offset - 1)).fetchone()
            if not previous or previous["packet"] != json_text(asdict(acquisition.packets[offset - 1])):
                raise ValueError("原始采集位置无法对齐。")
        checksum = header["digest"]
        pending = []
        for position, packet in enumerate(acquisition.packets[offset:], offset):
            text = json_text(asdict(packet))
            checksum = _chain(checksum, text)
            pending.append((identifier, position, text))
        db.executemany("INSERT INTO acquisition_packets(session_id,offset,packet) VALUES (?,?,?)", pending)
        db.execute("UPDATE acquisition_sessions SET packet_count=?,digest=? WHERE id=?", (count, checksum, identifier))
    return count


def load_recording(identifier, db_path=DB_PATH):
    with get_connection(db_path) as db:
        db.execute("BEGIN")
        header = db.execute("SELECT * FROM acquisition_sessions WHERE id=?", (identifier,)).fetchone()
        if not header or not 0 <= header["packet_count"] <= MAX_SAMPLES * 3:
            raise ValueError("采集档案缺失或缓冲位置无效。")
        acquisition = read_declaration(json.loads(header["declaration"]))
        checksum = _chain("", header["declaration"])
        packets = db.execute("SELECT offset,packet FROM acquisition_packets WHERE session_id=? "
                             "ORDER BY offset LIMIT ?", (identifier, MAX_SAMPLES * 3 + 1))
        for current, packet in enumerate(packets):
            if packet["offset"] != current:
                raise ValueError("采集档案存在位置间断。")
            checksum = _chain(checksum, packet["packet"])
            acquisition.push(Packet(**json.loads(packet["packet"])))
    if len(acquisition.packets) != header["packet_count"] or checksum != header["digest"]:
        raise ValueError("采集档案未通过摘要校验。")
    return acquisition


def recordings(db_path=DB_PATH):
    with get_connection(db_path) as db:
        return [dict(row) for row in db.execute("SELECT id,label,packet_count,created_at FROM acquisition_sessions "
                                                "ORDER BY created_at DESC,rowid DESC LIMIT 50")]
