"""Tests for the safe, reproducible fictional demo-data generator."""

import csv
from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from rail_vehicle.demo_data import (
    CSV_HEADER,
    DEMO_SAMPLE_COUNT,
    DEMO_VEHICLE_CODE,
    generate_demo_csvs,
)
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.run_data import validate_run_csv


class DemoDataTests(unittest.TestCase):
    def test_generates_two_valid_continuous_demo_batches(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = generate_demo_csvs(Path(temporary_dir) / "demo")

            self.assertEqual(len(paths), 2)
            for path in paths:
                with self.subTest(file=path.name):
                    content = path.read_bytes()
                    rows = list(csv.DictReader(content.decode("utf-8").splitlines()))
                    self.assertEqual(tuple(rows[0]), CSV_HEADER)
                    self.assertEqual(len(rows), DEMO_SAMPLE_COUNT)
                    self.assertTrue(all(row["vehicle_id"] == DEMO_VEHICLE_CODE for row in rows))
                    timestamps = [datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) for row in rows]
                    self.assertEqual(
                        [(right - left).total_seconds() for left, right in zip(timestamps, timestamps[1:])],
                        [1] * (DEMO_SAMPLE_COUNT - 1),
                    )
                    validation = validate_run_csv(
                        content,
                        [{"id": 1, "vehicle_code": DEMO_VEHICLE_CODE}],
                    )
                    self.assertTrue(validation.is_valid, validation.errors)
                    self.assertEqual(validation.source_row_count, DEMO_SAMPLE_COUNT)
                    anomalies = detect_anomalies(validation.rows, 1.0)
                    self.assertTrue(anomalies["points"])
                    self.assertTrue(anomalies["events"])

    def test_refuses_to_overwrite_existing_demo_file(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            output_dir = Path(temporary_dir)
            first_file = output_dir / "demo_run_1.csv"
            first_file.write_text("keep me", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                generate_demo_csvs(output_dir)

            self.assertEqual(first_file.read_text(encoding="utf-8"), "keep me")
            self.assertFalse((output_dir / "demo_run_2.csv").exists())


if __name__ == "__main__":
    unittest.main()
