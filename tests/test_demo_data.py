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
from rail_vehicle.batch_comparison import compare_run_batches
from rail_vehicle.db import init_db
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.run_data import (
    import_run_data,
    list_batch_samples,
    list_vehicle_batches,
    validate_run_csv,
)
from rail_vehicle.vehicle_data import add_vehicle


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

    def test_generated_demo_runs_complete_import_analysis_and_comparison_flow(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            db_path = root / "demo.sqlite3"
            init_db(db_path)
            vehicle_id = add_vehicle(
                {
                    "vehicle_code": DEMO_VEHICLE_CODE,
                    "vehicle_type": "虚构演示车辆",
                    "vehicle_length_m": 20,
                    "mass_kg": 40000,
                    "axle_count": 4,
                    "bogie_count": 2,
                    "max_speed_kmh": 120,
                    "notes": "",
                },
                db_path,
            )
            vehicles = [{"id": vehicle_id, "vehicle_code": DEMO_VEHICLE_CODE}]
            paths = generate_demo_csvs(root / "demo")
            imported_batch_ids = []

            for path in paths:
                validation = validate_run_csv(path.read_bytes(), vehicles)
                self.assertTrue(validation.is_valid, validation.errors)
                imported_batch_ids.append(
                    import_run_data(path.name, validation, db_path)
                )

            batch_descriptors = []
            for batch in list_vehicle_batches(vehicle_id, db_path):
                self.assertEqual(batch["sample_count"], DEMO_SAMPLE_COUNT)
                samples = list_batch_samples(vehicle_id, batch["id"], db_path)
                self.assertEqual(
                    analyze_run_data(samples)["sample_count"], DEMO_SAMPLE_COUNT
                )
                self.assertTrue(detect_anomalies(samples, 1.0)["events"])
                batch_descriptors.append(
                    {
                        "batch_id": batch["id"],
                        "label": batch["file_name"],
                        "samples": samples,
                    }
                )

            comparisons = compare_run_batches(batch_descriptors)

            self.assertEqual(len(imported_batch_ids), 2)
            self.assertEqual(len(comparisons), 2)
            self.assertTrue(
                all(
                    item["sample_count"] == DEMO_SAMPLE_COUNT
                    for item in comparisons
                )
            )
            self.assertTrue(
                all(
                    item["series"][0]["relative_time_seconds"] == 0
                    for item in comparisons
                )
            )

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
