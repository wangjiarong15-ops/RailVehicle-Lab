"""CSV validation and persistence for vehicle running samples."""

import csv
import io
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rail_vehicle.db import DB_PATH, get_connection


REQUIRED_COLUMNS = (
    "timestamp",
    "vehicle_id",
    "speed_kmh",
    "lateral_accel",
    "vertical_accel",
)
MAX_SPEED_KMH = 600.0
MAX_ABS_ACCELERATION = 100.0
PREVIEW_LIMIT = 20
ERROR_LIMIT = 50


@dataclass
class CsvValidationResult:
    """Parsed rows, raw preview, and any validation errors."""

    rows: list[dict[str, Any]]
    preview: list[dict[str, Any]]
    errors: list[str]
    source_row_count: int

    @property
    def is_valid(self) -> bool:
        return not self.errors and bool(self.rows)


def validate_run_csv(
    content: bytes | str, vehicles: list[dict[str, Any]]
) -> CsvValidationResult:
    """Parse and validate a CSV file without writing any database data."""
    errors: list[str] = []
    try:
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    except UnicodeDecodeError:
        return CsvValidationResult(
            [], [], ["CSV 文件必须使用 UTF-8 编码（可包含 BOM）。"], 0
        )
    text = text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        headers = next(reader, None)
        if not headers:
            return CsvValidationResult([], [], ["CSV 文件为空或缺少表头。"], 0)
        headers = [header.strip() for header in headers]
        if any(not header for header in headers):
            errors.append("CSV 表头不能包含空列名。")
        duplicates = sorted({header for header in headers if headers.count(header) > 1})
        if duplicates:
            errors.append(f"CSV 存在重复列名：{', '.join(duplicates)}。")
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            errors.append(f"CSV 缺少必需字段：{', '.join(missing)}。")
        if errors:
            return CsvValidationResult([], [], errors, 0)

        vehicle_ids = {
            vehicle["vehicle_code"]: vehicle["id"]
            for vehicle in vehicles
        }
        normalized_rows: list[dict[str, Any]] = []
        preview: list[dict[str, Any]] = []
        source_row_count = 0
        for row_number, values in enumerate(reader, start=2):
            if not values or all(not value.strip() for value in values):
                continue
            source_row_count += 1
            raw = {
                header: values[index] if index < len(values) else ""
                for index, header in enumerate(headers)
            }
            if len(preview) < PREVIEW_LIMIT:
                preview.append(raw)

            row_errors: list[str] = []
            if len(values) > len(headers):
                row_errors.append("列数超过表头定义")
            missing_values = [
                column for column in REQUIRED_COLUMNS if not raw.get(column, "").strip()
            ]
            if missing_values:
                row_errors.append(f"以下字段为空：{', '.join(missing_values)}")

            timestamp_text = raw.get("timestamp", "").strip()
            parsed_timestamp: str | None = None
            if timestamp_text:
                try:
                    if "T" not in timestamp_text and " " not in timestamp_text:
                        raise ValueError("时间戳必须同时包含日期和时间")
                    parsed = datetime.fromisoformat(
                        timestamp_text[:-1] + "+00:00"
                        if timestamp_text.endswith(("Z", "z"))
                        else timestamp_text
                    )
                    parsed_timestamp = parsed.isoformat(sep=" ")
                except ValueError:
                    row_errors.append(
                        "timestamp 格式无效，请使用 ISO 8601 日期时间（例如 2026-10-08T09:30:00）"
                    )

            vehicle_code = raw.get("vehicle_id", "").strip()
            vehicle_db_id = vehicle_ids.get(vehicle_code)
            if vehicle_code and vehicle_db_id is None:
                row_errors.append(f"车辆编号“{vehicle_code}”不存在")

            number_values: dict[str, float] = {}
            for column in ("speed_kmh", "lateral_accel", "vertical_accel"):
                value_text = raw.get(column, "").strip()
                if not value_text:
                    continue
                try:
                    value = float(value_text)
                except ValueError:
                    row_errors.append(f"{column} 必须是数字")
                    continue
                if not math.isfinite(value):
                    row_errors.append(f"{column} 必须是有限数值")
                    continue
                number_values[column] = value

            speed = number_values.get("speed_kmh")
            if speed is not None and not 0 <= speed <= MAX_SPEED_KMH:
                row_errors.append(f"speed_kmh 必须在 0 至 {MAX_SPEED_KMH:g} 之间")
            for column in ("lateral_accel", "vertical_accel"):
                acceleration = number_values.get(column)
                if acceleration is not None and abs(acceleration) > MAX_ABS_ACCELERATION:
                    row_errors.append(
                        f"{column} 绝对值不能超过 {MAX_ABS_ACCELERATION:g}"
                    )

            if row_errors:
                errors.extend(f"第 {row_number} 行：{message}。" for message in row_errors)
            elif parsed_timestamp is not None and vehicle_db_id is not None:
                normalized_rows.append(
                    {
                        "timestamp": parsed_timestamp,
                        "vehicle_id": vehicle_db_id,
                        "speed_kmh": number_values["speed_kmh"],
                        "lateral_accel": number_values["lateral_accel"],
                        "vertical_accel": number_values["vertical_accel"],
                    }
                )
            if len(errors) >= ERROR_LIMIT:
                errors.append("错误数量较多，已停止显示后续错误。")
                break

    except csv.Error as exc:
        errors.append(f"CSV 解析失败：{exc}")
        return CsvValidationResult([], [], errors, 0)

    if source_row_count == 0:
        errors.append("CSV 中没有可导入的数据行。")
    return CsvValidationResult(normalized_rows, preview, errors, source_row_count)


def import_run_data(
    file_name: str,
    validation: CsvValidationResult,
    db_path: Path | str = DB_PATH,
) -> int:
    """Atomically save a validated batch and its running samples."""
    if not validation.is_valid:
        raise ValueError("CSV 校验未通过，不能导入数据库。")
    if not file_name.strip():
        raise ValueError("导入文件名不能为空。")

    try:
        with get_connection(db_path) as connection:
            cursor = connection.execute(
                "INSERT INTO import_batches (file_name, row_count) VALUES (?, ?)",
                (Path(file_name).name, len(validation.rows)),
            )
            batch_id = int(cursor.lastrowid)
            connection.executemany(
                """INSERT INTO run_samples
                   (batch_id, vehicle_id, timestamp, speed_kmh, lateral_accel, vertical_accel)
                   VALUES (:batch_id, :vehicle_id, :timestamp, :speed_kmh,
                           :lateral_accel, :vertical_accel)""",
                [{**row, "batch_id": batch_id} for row in validation.rows],
            )
            return batch_id
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"数据写入失败，已回滚本次导入：{exc}") from exc


def list_import_batches(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Return import history, newest batch first."""
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """SELECT id, file_name, row_count, imported_at
               FROM import_batches ORDER BY imported_at DESC, id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def list_vehicle_batches(
    vehicle_id: int, db_path: Path | str = DB_PATH
) -> list[dict[str, Any]]:
    """List import batches containing samples for the selected vehicle."""
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """SELECT b.id, b.file_name, b.imported_at, COUNT(s.id) AS sample_count
               FROM import_batches AS b
               JOIN run_samples AS s ON s.batch_id = b.id
               WHERE s.vehicle_id = ?
               GROUP BY b.id, b.file_name, b.imported_at
               ORDER BY b.imported_at DESC, b.id DESC""",
            (vehicle_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_batch_samples(
    vehicle_id: int, batch_id: int, db_path: Path | str = DB_PATH
) -> list[dict[str, Any]]:
    """Fetch one vehicle's samples from a selected import batch."""
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """SELECT timestamp, speed_kmh, lateral_accel, vertical_accel
               FROM run_samples
               WHERE vehicle_id = ? AND batch_id = ?
               ORDER BY timestamp, id""",
            (vehicle_id, batch_id),
        ).fetchall()
    return [dict(row) for row in rows]
