"""CSV validation and persistence for vehicle running samples."""

import csv
import io
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rail_vehicle.data_quality import summarize_timestamps
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
MAX_CSV_SIZE_MB = 50
MAX_CSV_SIZE_BYTES = MAX_CSV_SIZE_MB * 1024 * 1024


@dataclass
class CsvValidationResult:
    """Validation summary and source needed to stream valid rows on demand."""

    preview: list[dict[str, Any]]
    errors: list[str]
    source_row_count: int
    distinct_vehicle_count: int = 0
    minimum_timestamp: str | None = None
    maximum_timestamp: str | None = None
    sampling_summary: dict[str, Any] | None = None
    _source_content: bytes | str | None = field(default=None, repr=False)
    _vehicle_ids: dict[str, int] = field(default_factory=dict, repr=False)

    @property
    def is_valid(self) -> bool:
        return not self.errors and self.source_row_count > 0 and self._source_content is not None

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Materialize validated rows only for callers that explicitly need them."""
        return list(self.iter_rows())

    def iter_rows(self):
        """Reparse the validated immutable upload and yield rows incrementally."""
        if not self.is_valid:
            return
        yield from _iter_normalized_csv_rows(self._source_content, self._vehicle_ids)


def _make_csv_reader(content: bytes | str):
    if isinstance(content, bytes):
        stream = io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="")
    else:
        stream = io.StringIO(content.lstrip("\ufeff"), newline="")
    return csv.reader(stream, strict=True)


def _read_headers(reader) -> tuple[list[str] | None, list[str]]:
    headers = next(reader, None)
    if not headers:
        return None, ["CSV 文件为空或缺少表头。"]

    headers = [header.strip() for header in headers]
    errors: list[str] = []
    if any(not header for header in headers):
        errors.append("CSV 表头不能包含空列名。")
    duplicates = sorted(
        header for header, count in Counter(headers).items() if count > 1
    )
    if duplicates:
        errors.append(f"CSV 存在重复列名：{', '.join(duplicates)}。")
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        errors.append(f"CSV 缺少必需字段：{', '.join(missing)}。")
    return headers, errors


def _normalize_csv_row(
    values: list[str],
    headers: list[str],
    vehicle_ids: dict[str, int],
) -> tuple[dict[str, Any] | None, dict[str, str], list[str]]:
    raw = {
        header: values[index] if index < len(values) else ""
        for index, header in enumerate(headers)
    }
    if len(values) > len(headers):
        row_errors = ["列数超过表头定义"]
    else:
        row_errors = []

    missing_values = [
        column for column in REQUIRED_COLUMNS if not raw.get(column, "").strip()
    ]
    if missing_values:
        row_errors.append(f"以下字段为空：{', '.join(missing_values)}")

    parsed_timestamp: str | None = None
    timestamp_text = raw.get("timestamp", "").strip()
    if timestamp_text:
        try:
            if "T" not in timestamp_text and " " not in timestamp_text:
                raise ValueError("时间戳必须同时包含日期和时间")
            parsed = datetime.fromisoformat(
                timestamp_text[:-1] + "+00:00"
                if timestamp_text.endswith(("Z", "z"))
                else timestamp_text
            )
            if parsed.tzinfo is not None:
                # Reject syntactically valid edge timestamps that cannot be
                # represented as UTC within datetime's supported year range.
                parsed.astimezone(timezone.utc)
            parsed_timestamp = parsed.isoformat(sep=" ")
        except OverflowError:
            row_errors.append("timestamp 超出可支持的 UTC 时间范围")
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
            row_errors.append(f"{column} 绝对值不能超过 {MAX_ABS_ACCELERATION:g}")

    if row_errors or parsed_timestamp is None or vehicle_db_id is None:
        return None, raw, row_errors
    return (
        {
            "timestamp": parsed_timestamp,
            "vehicle_id": vehicle_db_id,
            "speed_kmh": number_values["speed_kmh"],
            "lateral_accel": number_values["lateral_accel"],
            "vertical_accel": number_values["vertical_accel"],
        },
        raw,
        [],
    )


def _iter_normalized_csv_rows(content: bytes | str, vehicle_ids: dict[str, int]):
    """Yield validated normalized rows; fail closed if the source changed or is malformed."""
    try:
        reader = _make_csv_reader(content)
        headers, header_errors = _read_headers(reader)
        if header_errors or headers is None:
            raise ValueError("CSV 内容与校验结果不一致。")
        for values in reader:
            if not values or all(not value.strip() for value in values):
                continue
            row, _raw, row_errors = _normalize_csv_row(values, headers, vehicle_ids)
            if row_errors or row is None:
                raise ValueError("CSV 内容与校验结果不一致。")
            yield row
    except (csv.Error, UnicodeDecodeError):
        raise ValueError("CSV 内容与校验结果不一致。") from None


def validate_run_csv(
    content: bytes | str, vehicles: list[dict[str, Any]]
) -> CsvValidationResult:
    """Parse and validate a CSV file without writing any database data."""
    if isinstance(content, bytes):
        content_size = len(content)
    else:
        try:
            content_size = len(content.encode("utf-8"))
        except UnicodeEncodeError:
            return CsvValidationResult(
                [], ["CSV 文件必须使用 UTF-8 编码（可包含 BOM）。"], 0
            )
    if content_size > MAX_CSV_SIZE_BYTES:
        return CsvValidationResult(
            [], [f"CSV 文件超过 {MAX_CSV_SIZE_MB} MB 大小上限。"], 0
        )
    vehicle_ids = {vehicle["vehicle_code"]: vehicle["id"] for vehicle in vehicles}
    errors: list[str] = []
    preview: list[dict[str, str]] = []
    source_row_count = 0
    distinct_vehicle_ids: set[int] = set()
    minimum_timestamp: str | None = None
    maximum_timestamp: str | None = None
    minimum_timestamp_order: datetime | None = None
    maximum_timestamp_order: datetime | None = None
    sampling_timestamps: list[datetime] = []
    error_details_truncated = False
    try:
        reader = _make_csv_reader(content)
        headers, header_errors = _read_headers(reader)
        if headers is None or header_errors:
            return CsvValidationResult([], header_errors, 0)
        for row_number, values in enumerate(reader, start=2):
            if not values or all(not value.strip() for value in values):
                continue
            source_row_count += 1
            if error_details_truncated:
                if len(preview) < PREVIEW_LIMIT:
                    preview.append(
                        {
                            header: values[index] if index < len(values) else ""
                            for index, header in enumerate(headers)
                        }
                    )
                continue
            normalized_row, _raw, row_errors = _normalize_csv_row(
                values, headers, vehicle_ids
            )
            if len(preview) < PREVIEW_LIMIT:
                preview.append(_raw)
            if row_errors:
                row_messages = [
                    f"第 {row_number} 行：{message}。" for message in row_errors
                ]
                remaining_error_slots = max(0, ERROR_LIMIT - len(errors))
                errors.extend(row_messages[:remaining_error_slots])
                if len(row_messages) > remaining_error_slots:
                    errors.append("错误数量较多，已停止显示后续错误。")
                    error_details_truncated = True
            elif normalized_row is not None:
                distinct_vehicle_ids.add(normalized_row["vehicle_id"])
                timestamp = normalized_row["timestamp"]
                timestamp_order = datetime.fromisoformat(timestamp)
                if timestamp_order.tzinfo is None:
                    timestamp_order = timestamp_order.replace(tzinfo=timezone.utc)
                timestamp_order = timestamp_order.astimezone(timezone.utc)
                sampling_timestamps.append(timestamp_order)
                if (
                    minimum_timestamp_order is None
                    or timestamp_order < minimum_timestamp_order
                ):
                    minimum_timestamp_order = timestamp_order
                    minimum_timestamp = timestamp
                if (
                    maximum_timestamp_order is None
                    or timestamp_order > maximum_timestamp_order
                ):
                    maximum_timestamp_order = timestamp_order
                    maximum_timestamp = timestamp
    except UnicodeDecodeError:
        return CsvValidationResult(
            [], ["CSV 文件必须使用 UTF-8 编码（可包含 BOM）。"], 0
        )
    except csv.Error:
        return CsvValidationResult(
            [], ["CSV 格式无效，请检查引号、列分隔符和字段长度。"], 0
        )

    if source_row_count == 0:
        errors.append("CSV 中没有可导入的数据行。")
    return CsvValidationResult(
        preview=preview,
        errors=errors,
        source_row_count=source_row_count,
        distinct_vehicle_count=len(distinct_vehicle_ids),
        minimum_timestamp=minimum_timestamp,
        maximum_timestamp=maximum_timestamp,
        sampling_summary=(
            summarize_timestamps(sampling_timestamps) if not errors else None
        ),
        _source_content=content,
        _vehicle_ids=vehicle_ids,
    )


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
                (Path(file_name).name, validation.source_row_count),
            )
            batch_id = int(cursor.lastrowid)

            inserted_row_count = 0

            def rows_for_import():
                nonlocal inserted_row_count
                for row in validation.iter_rows():
                    inserted_row_count += 1
                    yield {**row, "batch_id": batch_id}

            connection.executemany(
                """INSERT INTO run_samples
                   (batch_id, vehicle_id, timestamp, speed_kmh, lateral_accel, vertical_accel)
                   VALUES (:batch_id, :vehicle_id, :timestamp, :speed_kmh,
                           :lateral_accel, :vertical_accel)""",
                rows_for_import(),
            )
            if inserted_row_count != validation.source_row_count:
                raise ValueError("CSV row count changed after validation")
            return batch_id
    except ValueError:
        raise ValueError("CSV 内容在导入阶段无法重新校验，本次导入已回滚。") from None
    except sqlite3.IntegrityError:
        raise ValueError("数据完整性校验失败，本次导入已回滚。") from None
    except sqlite3.Error:
        raise ValueError("数据库暂时无法保存运行数据，本次导入已回滚，请稍后重试。") from None
    except OSError:
        raise ValueError("无法访问数据库文件，本次导入未完成。请检查本地目录权限后重试。") from None


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
    samples = [dict(row) for row in rows]

    def chronological_key(sample: dict[str, Any]) -> tuple[int, datetime]:
        try:
            timestamp = datetime.fromisoformat(sample["timestamp"])
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            else:
                timestamp = timestamp.astimezone(timezone.utc)
            return 0, timestamp
        except (OverflowError, TypeError, ValueError):
            # Keep malformed legacy rows visible to callers, which report the
            # invalid timestamp, while sorting valid records deterministically.
            return 1, datetime.max.replace(tzinfo=timezone.utc)

    return sorted(samples, key=chronological_key)
