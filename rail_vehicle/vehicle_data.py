"""Vehicle validation and SQLite persistence operations."""

import math
import sqlite3
from pathlib import Path
from typing import Any

from rail_vehicle.db import DB_PATH, get_connection


class DuplicateVehicleCodeError(ValueError):
    """Raised when a vehicle code is already in use."""


class VehicleNotFoundError(ValueError):
    """Raised when an edit targets a vehicle that no longer exists."""


def validate_vehicle_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate the fields entered for a vehicle."""
    vehicle_code = str(data.get("vehicle_code", "")).strip()
    vehicle_type = str(data.get("vehicle_type", "")).strip()
    notes = str(data.get("notes", "")).strip()
    errors: list[str] = []

    if not vehicle_code:
        errors.append("车辆编号不能为空。")
    if len(vehicle_code) > 50:
        errors.append("车辆编号不能超过 50 个字符。")
    if not vehicle_type:
        errors.append("车型不能为空。")
    if len(vehicle_type) > 100:
        errors.append("车型不能超过 100 个字符。")

    def positive_number(field: str, label: str) -> float | None:
        try:
            value = float(data[field])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{label}必须是大于 0 的数字。")
            return None
        if not math.isfinite(value) or value <= 0:
            errors.append(f"{label}必须是大于 0 的有限数字。")
            return None
        return value

    def positive_integer(field: str, label: str) -> int | None:
        value = data.get(field)
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors.append(f"{label}必须是大于 0 的整数。")
            return None
        if not math.isfinite(number) or not number.is_integer() or number <= 0:
            errors.append(f"{label}必须是大于 0 的整数。")
            return None
        return int(number)

    vehicle_length_m = positive_number("vehicle_length_m", "车辆长度")
    mass_kg = positive_number("mass_kg", "车辆质量")
    axle_count = positive_integer("axle_count", "轴数")
    bogie_count = positive_integer("bogie_count", "转向架数量")
    max_speed_kmh = positive_number("max_speed_kmh", "最高运行速度")
    if len(notes) > 2000:
        errors.append("备注不能超过 2000 个字符。")

    if errors:
        raise ValueError("\n".join(errors))

    return {
        "vehicle_code": vehicle_code,
        "vehicle_type": vehicle_type,
        "vehicle_length_m": vehicle_length_m,
        "mass_kg": mass_kg,
        "axle_count": axle_count,
        "bogie_count": bogie_count,
        "max_speed_kmh": max_speed_kmh,
        "notes": notes,
    }


def list_vehicles(db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    """Return all vehicles ordered by their vehicle code."""
    with get_connection(db_path) as connection:
        rows = connection.execute(
            """SELECT id, vehicle_code, vehicle_type, vehicle_length_m, mass_kg,
                      axle_count, bogie_count, max_speed_kmh, notes, created_at
               FROM vehicles ORDER BY vehicle_code COLLATE NOCASE"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_vehicle(vehicle_id: int, db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    """Return one vehicle by its database identifier."""
    with get_connection(db_path) as connection:
        row = connection.execute(
            """SELECT id, vehicle_code, vehicle_type, vehicle_length_m, mass_kg,
                      axle_count, bogie_count, max_speed_kmh, notes, created_at
               FROM vehicles WHERE id = ?""",
            (vehicle_id,),
        ).fetchone()
    return dict(row) if row else None


def add_vehicle(data: dict[str, Any], db_path: Path | str = DB_PATH) -> int:
    """Validate and insert a vehicle, returning its database identifier."""
    vehicle = validate_vehicle_data(data)
    try:
        with get_connection(db_path) as connection:
            cursor = connection.execute(
                """INSERT INTO vehicles
                   (vehicle_code, vehicle_type, vehicle_length_m, mass_kg,
                    axle_count, bogie_count, max_speed_kmh, notes)
                   VALUES (:vehicle_code, :vehicle_type, :vehicle_length_m, :mass_kg,
                           :axle_count, :bogie_count, :max_speed_kmh, :notes)""",
                vehicle,
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        if "vehicles.vehicle_code" in str(exc):
            raise DuplicateVehicleCodeError("该车辆编号已存在，请使用其他编号。") from exc
        raise


def update_vehicle(
    vehicle_id: int, data: dict[str, Any], db_path: Path | str = DB_PATH
) -> None:
    """Validate and update an existing vehicle."""
    vehicle = validate_vehicle_data(data)
    try:
        with get_connection(db_path) as connection:
            cursor = connection.execute(
                """UPDATE vehicles SET
                   vehicle_code = :vehicle_code, vehicle_type = :vehicle_type,
                   vehicle_length_m = :vehicle_length_m, mass_kg = :mass_kg,
                   axle_count = :axle_count, bogie_count = :bogie_count,
                   max_speed_kmh = :max_speed_kmh, notes = :notes
                   WHERE id = :id""",
                {**vehicle, "id": vehicle_id},
            )
            if cursor.rowcount == 0:
                raise VehicleNotFoundError("找不到要修改的车辆，请刷新列表后重试。")
    except sqlite3.IntegrityError as exc:
        if "vehicles.vehicle_code" in str(exc):
            raise DuplicateVehicleCodeError("该车辆编号已存在，请使用其他编号。") from exc
        raise
