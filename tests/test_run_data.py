"""Tests for CSV validation and transactional running-data imports."""

import tempfile
import unittest
from pathlib import Path

from rail_vehicle.db import get_connection, init_db
from rail_vehicle.run_data import (
    import_run_data,
    list_import_batches,
    list_batch_samples,
    list_vehicle_batches,
    validate_run_csv,
)
from rail_vehicle.vehicle_data import add_vehicle


HEADER = "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel"
GOOD_ROW = "2026-10-08T09:30:00,RV-001,80,0.12,-0.04"


class RunDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "run_data.sqlite3"
        init_db(self.db_path)
        self.vehicle_id = add_vehicle(
            {
                "vehicle_code": "RV-001",
                "vehicle_type": "测试车辆",
                "vehicle_length_m": 20,
                "mass_kg": 40000,
                "axle_count": 4,
                "bogie_count": 2,
                "max_speed_kmh": 120,
                "notes": "",
            },
            self.db_path,
        )
        self.vehicles = [
            {"id": self.vehicle_id, "vehicle_code": "RV-001"}
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def validate(self, text):
        return validate_run_csv(text, self.vehicles)

    def counts(self):
        with get_connection(self.db_path) as connection:
            batch_count = connection.execute(
                "SELECT COUNT(*) FROM import_batches"
            ).fetchone()[0]
            sample_count = connection.execute(
                "SELECT COUNT(*) FROM run_samples"
            ).fetchone()[0]
        return batch_count, sample_count

    def test_valid_csv_is_imported_as_batch_and_samples(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}\n")
        self.assertTrue(validation.is_valid)
        batch_id = import_run_data("trip.csv", validation, self.db_path)

        batches = list_import_batches(self.db_path)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["id"], batch_id)
        self.assertEqual(batches[0]["file_name"], "trip.csv")
        self.assertEqual(batches[0]["row_count"], 1)
        with get_connection(self.db_path) as connection:
            sample = connection.execute(
                "SELECT * FROM run_samples WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        self.assertEqual(sample["vehicle_id"], self.vehicle_id)
        self.assertEqual(sample["speed_kmh"], 80)
        self.assertEqual(sample["lateral_accel"], 0.12)
        self.assertEqual(sample["vertical_accel"], -0.04)

    def test_batch_and_sample_queries_filter_by_vehicle_and_batch(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}")
        first_batch = import_run_data("first.csv", validation, self.db_path)
        second_batch = import_run_data(
            "second.csv",
            self.validate(f"{HEADER}\n{GOOD_ROW.replace('80', '90')}"),
            self.db_path,
        )

        batches = list_vehicle_batches(self.vehicle_id, self.db_path)
        self.assertEqual({batch["id"] for batch in batches}, {first_batch, second_batch})
        self.assertTrue(all(batch["sample_count"] == 1 for batch in batches))
        samples = list_batch_samples(self.vehicle_id, first_batch, self.db_path)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["speed_kmh"], 80)

    def test_utf8_bom_and_iso_timestamp_are_supported(self):
        validation = self.validate((f"{HEADER}\n{GOOD_ROW}\n").encode("utf-8-sig"))
        self.assertTrue(validation.is_valid, validation.errors)
        self.assertEqual(validation.source_row_count, 1)

    def test_missing_columns_are_rejected(self):
        validation = self.validate("timestamp,vehicle_id,speed_kmh\n" + GOOD_ROW)
        self.assertFalse(validation.is_valid)
        self.assertIn("lateral_accel", validation.errors[0])
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_timestamp_unknown_vehicle_and_empty_value_are_rejected(self):
        text = (
            f"{HEADER}\n"
            "not-a-date,RV-001,80,0.1,0.2\n"
            "2026-10-08T09:30:00,UNKNOWN,80,0.1,0.2\n"
            "2026-10-08T09:30:00,RV-001,,0.1,0.2\n"
        )
        validation = self.validate(text)
        self.assertFalse(validation.is_valid)
        messages = "\n".join(validation.errors)
        self.assertIn("timestamp 格式无效", messages)
        self.assertIn("UNKNOWN", messages)
        self.assertIn("speed_kmh", messages)
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_numeric_values_and_ranges_are_rejected(self):
        text = (
            f"{HEADER}\n"
            "2026-10-08T09:30:00,RV-001,fast,0,0\n"
            "2026-10-08T09:30:01,RV-001,601,101,0\n"
        )
        validation = self.validate(text)
        self.assertFalse(validation.is_valid)
        messages = "\n".join(validation.errors)
        self.assertIn("必须是数字", messages)
        self.assertIn("0 至 600", messages)
        self.assertIn("绝对值不能超过 100", messages)

    def test_invalid_file_cannot_write_any_rows_or_batch(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}\ninvalid,RV-001,80,0,0")
        self.assertFalse(validation.is_valid)
        with self.assertRaisesRegex(ValueError, "校验未通过"):
            import_run_data("trip.csv", validation, self.db_path)
        self.assertEqual(self.counts(), (0, 0))

    def test_write_failure_rolls_back_batch_and_all_samples(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}")
        validation.rows[0]["vehicle_id"] = 99999
        with self.assertRaisesRegex(ValueError, "已回滚"):
            import_run_data("trip.csv", validation, self.db_path)
        self.assertEqual(self.counts(), (0, 0))

    def test_preview_and_row_count_are_available_before_import(self):
        text = f"{HEADER}\n{GOOD_ROW}\n{GOOD_ROW.replace('80', '81')}\n"
        validation = self.validate(text)
        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.source_row_count, 2)
        self.assertEqual(len(validation.preview), 2)
        self.assertEqual(validation.preview[0]["vehicle_id"], "RV-001")

    def test_empty_file_or_empty_data_is_rejected(self):
        self.assertFalse(self.validate("").is_valid)
        validation = self.validate(f"{HEADER}\n")
        self.assertFalse(validation.is_valid)
        self.assertIn("没有可导入的数据行", validation.errors[0])

    def test_date_without_time_is_not_a_valid_timestamp(self):
        validation = self.validate(
            f"{HEADER}\n2026-10-08,RV-001,80,0.1,-0.1"
        )
        self.assertFalse(validation.is_valid)
        self.assertIn("timestamp 格式无效", validation.errors[0])


if __name__ == "__main__":
    unittest.main()
