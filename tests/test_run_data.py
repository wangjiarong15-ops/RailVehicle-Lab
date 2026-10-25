"""Tests for CSV validation and transactional running-data imports."""

import sqlite3
import tempfile
import unittest
from unittest.mock import patch
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

    def test_validation_summary_matches_rows_without_eagerly_storing_them(self):
        text = (
            f"{HEADER}\n"
            "2026-10-08T09:30:00,RV-001,80,0.12,-0.04\n"
            "2026-10-08T09:30:02,RV-001,81,0.15,-0.02\n"
        )

        validation = self.validate(text)

        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.source_row_count, 2)
        self.assertEqual(validation.distinct_vehicle_count, 1)
        self.assertEqual(validation.minimum_timestamp, "2026-10-08 09:30:00")
        self.assertEqual(validation.maximum_timestamp, "2026-10-08 09:30:02")
        self.assertEqual(len(validation.preview), 2)
        self.assertNotIn("rows", validation.__dict__)

    def test_sampling_overview_uses_validated_iterator_without_importing(self):
        text = (
            f"{HEADER}\n"
            "2026-10-08T09:30:00Z,RV-001,80,0.12,-0.04\n"
            "2026-10-08T09:30:00+00:00,RV-001,81,0.15,-0.02\n"
            "2026-10-08T09:30:02Z,RV-001,82,0.18,-0.01\n"
        )

        validation = self.validate(text)
        summary = validation.sampling_summary

        self.assertTrue(validation.is_valid, validation.errors)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["duplicate_timestamp_rows"], 1)
        self.assertEqual(summary["interval_min_seconds"], 2)
        self.assertEqual(summary["interval_median_seconds"], 2)
        self.assertEqual(summary["interval_max_seconds"], 2)
        self.assertEqual(self.counts(), (0, 0))

    def test_validation_time_range_compares_mixed_offsets_chronologically(self):
        text = (
            f"{HEADER}\n"
            "2026-10-08T10:00:00+02:00,RV-001,80,0.12,-0.04\n"
            "2026-10-08T09:00:00+00:00,RV-001,81,0.15,-0.02\n"
        )

        validation = self.validate(text)

        self.assertTrue(validation.is_valid, validation.errors)
        self.assertEqual(validation.minimum_timestamp, "2026-10-08 10:00:00+02:00")
        self.assertEqual(validation.maximum_timestamp, "2026-10-08 09:00:00+00:00")

    def test_import_passes_sample_rows_to_sqlite_as_a_lazy_iterator(self):
        validation = self.validate(
            f"{HEADER}\n{GOOD_ROW}\n{GOOD_ROW.replace('80', '81')}\n"
        )

        class FakeCursor:
            lastrowid = 42

        class Transaction:
            received_iterator = False
            inserted_rows = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, *_args):
                return FakeCursor()

            def executemany(self, _sql, parameters):
                self.received_iterator = iter(parameters) is parameters
                self.inserted_rows = list(parameters)

        transaction = Transaction()
        with patch("rail_vehicle.run_data.get_connection", return_value=transaction):
            batch_id = import_run_data("trip.csv", validation, self.db_path)

        self.assertEqual(batch_id, 42)
        self.assertTrue(transaction.received_iterator)
        self.assertEqual(len(transaction.inserted_rows), 2)
        self.assertTrue(all(row["batch_id"] == 42 for row in transaction.inserted_rows))

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

    def test_batch_samples_are_ordered_by_absolute_time_across_offsets(self):
        text = (
            f"{HEADER}\n"
            "2026-10-08T10:00:00+02:00,RV-001,80,0.1,0.2\n"
            "2026-10-08T09:00:00+00:00,RV-001,81,0.2,0.3\n"
        )
        validation = self.validate(text)
        self.assertTrue(validation.is_valid, validation.errors)
        batch_id = import_run_data("offsets.csv", validation, self.db_path)

        samples = list_batch_samples(self.vehicle_id, batch_id, self.db_path)
        self.assertEqual(
            [sample["timestamp"] for sample in samples],
            ["2026-10-08 10:00:00+02:00", "2026-10-08 09:00:00+00:00"],
        )

    def test_legacy_sample_outside_utc_range_remains_visible_for_analysis_error(self):
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                "INSERT INTO import_batches (file_name, row_count) VALUES (?, ?)",
                ("legacy.csv", 1),
            )
            batch_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO run_samples
                   (batch_id, vehicle_id, timestamp, speed_kmh, lateral_accel, vertical_accel)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (batch_id, self.vehicle_id, "0001-01-01T00:00:00+14:00", 80, 0, 0),
            )

        samples = list_batch_samples(self.vehicle_id, batch_id, self.db_path)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["timestamp"], "0001-01-01T00:00:00+14:00")

    def test_utf8_bom_and_iso_timestamp_are_supported(self):
        validation = self.validate((f"{HEADER}\n{GOOD_ROW}\n").encode("utf-8-sig"))
        self.assertTrue(validation.is_valid, validation.errors)
        self.assertEqual(validation.source_row_count, 1)

    def test_valid_quoted_csv_fields_remain_supported(self):
        validation = self.validate(
            f'{HEADER}\n"2026-10-08T09:30:00","RV-001","80","0.12","-0.04"\n'
        )

        self.assertTrue(validation.is_valid, validation.errors)
        self.assertEqual(validation.rows[0]["speed_kmh"], 80)

    def test_invalid_utf8_bytes_are_rejected_during_streamed_decoding(self):
        validation = self.validate(
            f"{HEADER}\n{GOOD_ROW}\n".encode("utf-8") + b"\xff"
        )

        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.rows, [])
        self.assertIn("UTF-8", validation.errors[0])

    def test_unterminated_quoted_field_is_rejected_without_usable_rows(self):
        malformed_csv = (
            f'{HEADER}\n2026-10-08T09:30:00,RV-001,80,0.12,"-0.04\n'
        )

        validation = self.validate(malformed_csv)

        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.rows, [])
        self.assertEqual(validation.source_row_count, 0)
        self.assertIn("CSV 格式无效", validation.errors[0])

    def test_missing_columns_are_rejected(self):
        validation = self.validate("timestamp,vehicle_id,speed_kmh\n" + GOOD_ROW)
        self.assertFalse(validation.is_valid)
        self.assertIn("lateral_accel", validation.errors[0])
        self.assertEqual(self.counts(), (0, 0))

    def test_duplicate_headers_are_rejected(self):
        duplicate_header = (
            "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel,"
            "speed_kmh"
        )
        validation = self.validate(f"{duplicate_header}\n{GOOD_ROW},81")

        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.rows, [])
        self.assertTrue(any("重复列名：speed_kmh" in error for error in validation.errors))

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

    def test_error_detail_limit_still_counts_all_nonblank_data_rows(self):
        rows = [
            "2026-10-08T09:30:00,RV-001,fast,0.1,0.2",
            "2026-10-08T09:30:01,RV-001,fast,0.1,0.2",
            GOOD_ROW,
            "2026-10-08T09:30:03,RV-001,fast,0.1,0.2",
            GOOD_ROW.replace("80", "81"),
        ]
        with (
            patch("rail_vehicle.run_data.ERROR_LIMIT", 2),
            patch("rail_vehicle.run_data.PREVIEW_LIMIT", 2),
        ):
            validation = self.validate(f"{HEADER}\n" + "\n".join(rows))

        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.source_row_count, len(rows))
        self.assertEqual(len(validation.preview), 2)
        self.assertIn("已停止显示后续错误", validation.errors[-1])
        self.assertEqual(self.counts(), (0, 0))

    def test_error_detail_limit_is_not_exceeded_by_many_errors_in_one_row(self):
        rows = [
            "2026-10-08T09:30:00,RV-001,fast,sideways,nan",
            GOOD_ROW,
            GOOD_ROW.replace("80", "81"),
        ]
        with patch("rail_vehicle.run_data.ERROR_LIMIT", 2):
            validation = self.validate(f"{HEADER}\n" + "\n".join(rows))

        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.source_row_count, len(rows))
        self.assertEqual(len(validation.errors), 3)
        self.assertIn("已停止显示后续错误", validation.errors[-1])
        self.assertEqual(self.counts(), (0, 0))

    def test_csv_syntax_errors_after_error_limit_are_still_detected(self):
        text = (
            f"{HEADER}\n"
            "2026-10-08T09:30:00,RV-001,fast,0.1,0.2\n"
            f"{GOOD_ROW}\n"
            '2026-10-08T09:30:02,RV-001,80,0.1,"unterminated\n'
        )
        with patch("rail_vehicle.run_data.ERROR_LIMIT", 1):
            validation = self.validate(text)

        self.assertFalse(validation.is_valid)
        self.assertIn("CSV 格式无效", validation.errors[0])
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_file_cannot_write_any_rows_or_batch(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}\ninvalid,RV-001,80,0,0")
        self.assertFalse(validation.is_valid)
        with self.assertRaisesRegex(ValueError, "校验未通过"):
            import_run_data("trip.csv", validation, self.db_path)
        self.assertEqual(self.counts(), (0, 0))

    def test_write_failure_rolls_back_batch_and_all_samples(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}")
        with get_connection(self.db_path) as connection:
            connection.execute(
                "DELETE FROM vehicles WHERE id = ?", (self.vehicle_id,)
            )
        with self.assertRaisesRegex(ValueError, "已回滚"):
            import_run_data("trip.csv", validation, self.db_path)
        self.assertEqual(self.counts(), (0, 0))

    def test_second_pass_stream_error_rolls_back_batch_and_inserted_samples(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}")
        valid_row = {
            "timestamp": "2026-10-08 09:30:00",
            "vehicle_id": self.vehicle_id,
            "speed_kmh": 80.0,
            "lateral_accel": 0.12,
            "vertical_accel": -0.04,
        }

        def rows_then_error(*_args):
            yield valid_row
            raise ValueError("private parser detail")

        with patch(
            "rail_vehicle.run_data._iter_normalized_csv_rows", rows_then_error
        ):
            with self.assertRaisesRegex(ValueError, "导入阶段无法重新校验") as raised:
                import_run_data("trip.csv", validation, self.db_path)

        self.assertNotIn("private parser detail", str(raised.exception))
        self.assertEqual(self.counts(), (0, 0))

    def test_second_pass_row_count_mismatch_rolls_back_without_losing_history(self):
        original = self.validate(f"{HEADER}\n{GOOD_ROW}")
        existing_batch_id = import_run_data("existing.csv", original, self.db_path)
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}\n{GOOD_ROW}")
        valid_row = original.rows[0]

        for actual_count in (0, 1, 3):
            with self.subTest(actual_count=actual_count):
                with patch(
                    "rail_vehicle.run_data._iter_normalized_csv_rows",
                    return_value=iter([valid_row] * actual_count),
                ):
                    with self.assertRaisesRegex(ValueError, "已回滚") as raised:
                        import_run_data("mismatch.csv", validation, self.db_path)
                self.assertNotIn("CSV row count changed", str(raised.exception))
                self.assertEqual(self.counts(), (1, 1))
                history = list_import_batches(self.db_path)
                self.assertEqual(history[0]["id"], existing_batch_id)
                self.assertEqual(history[0]["row_count"], 1)

    def test_sqlite_operational_error_is_sanitized_after_transaction_rollback(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}")
        private_detail = "PRIVATE_DATABASE_PATH_OR_SQL_DETAIL"

        class FakeCursor:
            lastrowid = 123

        class FakeConnection:
            def execute(self, *_args, **_kwargs):
                return FakeCursor()

            def executemany(self, *_args, **_kwargs):
                raise sqlite3.OperationalError(private_detail)

        class Transaction:
            error_type = None

            def __enter__(self):
                return FakeConnection()

            def __exit__(self, error_type, _error, _traceback):
                self.error_type = error_type
                return False

        transaction = Transaction()
        with patch("rail_vehicle.run_data.get_connection", return_value=transaction):
            with self.assertRaisesRegex(ValueError, "数据库暂时无法保存") as raised:
                import_run_data("trip.csv", validation, self.db_path)

        self.assertIs(transaction.error_type, sqlite3.OperationalError)
        self.assertNotIn(private_detail, str(raised.exception))

    def test_database_filesystem_error_is_sanitized(self):
        validation = self.validate(f"{HEADER}\n{GOOD_ROW}")
        private_detail = "PRIVATE_LOCAL_DATABASE_PATH"
        with patch(
            "rail_vehicle.run_data.get_connection",
            side_effect=PermissionError(private_detail),
        ):
            with self.assertRaisesRegex(ValueError, "无法访问数据库文件") as raised:
                import_run_data("trip.csv", validation, self.db_path)

        self.assertNotIn(private_detail, str(raised.exception))

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

    def test_csv_over_configured_size_limit_is_rejected_before_parsing(self):
        with patch("rail_vehicle.run_data.MAX_CSV_SIZE_BYTES", 8):
            validation = self.validate(b"not parsed")

        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.rows, [])
        self.assertEqual(validation.source_row_count, 0)
        self.assertIn("大小上限", validation.errors[0])

    def test_csv_text_with_unencodable_surrogate_returns_validation_error(self):
        validation = self.validate("\ud800")

        self.assertFalse(validation.is_valid)
        self.assertEqual(validation.rows, [])
        self.assertIn("UTF-8", validation.errors[0])

    def test_date_without_time_is_not_a_valid_timestamp(self):
        validation = self.validate(
            f"{HEADER}\n2026-10-08,RV-001,80,0.1,-0.1"
        )
        self.assertFalse(validation.is_valid)
        self.assertIn("timestamp 格式无效", validation.errors[0])

    def test_timestamp_offset_outside_supported_utc_range_is_rejected(self):
        validation = self.validate(
            f"{HEADER}\n0001-01-01T00:00:00+14:00,RV-001,80,0.1,-0.1"
        )

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any("timestamp 超出可支持的 UTC 时间范围" in error for error in validation.errors)
        )
        self.assertEqual(self.counts(), (0, 0))


if __name__ == "__main__":
    unittest.main()
