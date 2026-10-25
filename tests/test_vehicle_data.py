"""Tests for vehicle validation and SQLite persistence."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from rail_vehicle.db import init_db
from rail_vehicle.vehicle_data import (
    DuplicateVehicleCodeError,
    VehicleNotFoundError,
    add_vehicle,
    get_vehicle,
    list_vehicles,
    update_vehicle,
    validate_vehicle_data,
)


def sample_vehicle(**overrides):
    values = {
        "vehicle_code": "RV-001",
        "vehicle_type": "城轨车辆",
        "vehicle_length_m": 19.5,
        "mass_kg": 38000,
        "axle_count": 4,
        "bogie_count": 2,
        "max_speed_kmh": 120,
        "notes": "测试车辆",
    }
    values.update(overrides)
    return values


class VehicleDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "vehicles.sqlite3"
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_and_read_vehicle_persists_all_fields(self):
        vehicle_id = add_vehicle(sample_vehicle(), self.db_path)

        # Read through a new SQLite connection, as happens on a later app rerun.
        saved = get_vehicle(vehicle_id, self.db_path)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["vehicle_code"], "RV-001")
        self.assertEqual(saved["vehicle_type"], "城轨车辆")
        self.assertEqual(saved["vehicle_length_m"], 19.5)
        self.assertEqual(saved["mass_kg"], 38000)
        self.assertEqual(saved["axle_count"], 4)
        self.assertEqual(saved["bogie_count"], 2)
        self.assertEqual(saved["max_speed_kmh"], 120)
        self.assertEqual(saved["notes"], "测试车辆")
        self.assertEqual(len(list_vehicles(self.db_path)), 1)

    def test_update_changes_vehicle_and_keeps_it_persisted(self):
        vehicle_id = add_vehicle(sample_vehicle(), self.db_path)
        update_vehicle(
            vehicle_id,
            sample_vehicle(vehicle_type="市域车辆", max_speed_kmh=160, notes="更新"),
            self.db_path,
        )

        saved = get_vehicle(vehicle_id, self.db_path)
        self.assertEqual(saved["vehicle_type"], "市域车辆")
        self.assertEqual(saved["max_speed_kmh"], 160)
        self.assertEqual(saved["notes"], "更新")

    def test_validation_rejects_empty_and_nonpositive_fields(self):
        with self.assertRaisesRegex(ValueError, "车辆编号不能为空"):
            validate_vehicle_data(sample_vehicle(vehicle_code="  "))
        with self.assertRaisesRegex(ValueError, "车辆长度必须"):
            validate_vehicle_data(sample_vehicle(vehicle_length_m=0))
        with self.assertRaisesRegex(ValueError, "轴数必须"):
            validate_vehicle_data(sample_vehicle(axle_count=2.5))

    def test_vehicle_code_must_be_unique(self):
        add_vehicle(sample_vehicle(), self.db_path)
        with self.assertRaises(DuplicateVehicleCodeError):
            add_vehicle(sample_vehicle(vehicle_type="另一车型"), self.db_path)

    def test_null_required_text_is_rejected_and_optional_notes_are_empty(self):
        for field in ("vehicle_code", "vehicle_type"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                add_vehicle(sample_vehicle(**{field: None}), self.db_path)
        self.assertEqual(list_vehicles(self.db_path), [])
        vehicle_id = add_vehicle(sample_vehicle(notes=None), self.db_path)
        self.assertEqual(get_vehicle(vehicle_id, self.db_path)["notes"], "")

    def test_integer_counts_preserve_precision_through_sqlite(self):
        # A float conversion silently rounds both of these integer values.
        for count in (2**53 + 1, 2**63 - 1):
            with self.subTest(count=count):
                vehicle_id = add_vehicle(
                    sample_vehicle(vehicle_code=f"RV-{count}", axle_count=str(count)),
                    self.db_path,
                )
                self.assertEqual(get_vehicle(vehicle_id, self.db_path)["axle_count"], count)

    def test_out_of_range_counts_do_not_insert_or_change_existing_vehicle(self):
        vehicle_id = add_vehicle(sample_vehicle(), self.db_path)
        original = get_vehicle(vehicle_id, self.db_path)
        for field in ("axle_count", "bogie_count"):
            for count in (2**63, "1e100", "4.0000000000000001"):
                with self.subTest(field=field, count=count):
                    invalid = sample_vehicle(**{field: count})
                    with self.assertRaises(ValueError):
                        add_vehicle(invalid, self.db_path)
                    with self.assertRaises(ValueError):
                        update_vehicle(vehicle_id, invalid, self.db_path)
                    self.assertEqual(get_vehicle(vehicle_id, self.db_path), original)
                    self.assertEqual(len(list_vehicles(self.db_path)), 1)

    def test_overflowing_float_input_reports_validation_error(self):
        with self.assertRaisesRegex(ValueError, "车辆质量必须"):
            validate_vehicle_data(sample_vehicle(mass_kg=10**400))

    def test_update_missing_vehicle_reports_not_found(self):
        with self.assertRaises(VehicleNotFoundError):
            update_vehicle(999, sample_vehicle(), self.db_path)

    def test_init_db_migrates_original_scaffold_table(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_code TEXT NOT NULL UNIQUE,
                    vehicle_type TEXT NOT NULL DEFAULT '',
                    mass_kg REAL,
                    axle_count INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                "INSERT INTO vehicles (vehicle_code, vehicle_type) VALUES (?, ?)",
                ("OLD-001", "既有车型"),
            )
            connection.commit()
        finally:
            connection.close()

        init_db(legacy_path)
        migrated = list_vehicles(legacy_path)
        self.assertEqual(len(migrated), 1)
        self.assertEqual(migrated[0]["vehicle_code"], "OLD-001")
        self.assertEqual(migrated[0]["vehicle_length_m"], 0)


if __name__ == "__main__":
    unittest.main()
