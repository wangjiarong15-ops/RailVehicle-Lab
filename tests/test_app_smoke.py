"""Basic Streamlit navigation smoke tests isolated from the local database."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import rail_vehicle.db as db
from rail_vehicle.pages import run_analysis, vehicles
from rail_vehicle.vehicle_data import list_vehicles


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AppSmokeTests(unittest.TestCase):
    def test_vehicle_and_run_analysis_pages_render_without_existing_data(self):
        with tempfile.TemporaryDirectory(prefix="rail-app-smoke-") as directory:
            db_path = Path(directory) / "smoke.sqlite3"
            initialize_database = db.init_db
            initialize_temp_database = lambda: initialize_database(db_path)
            read_temp_vehicles = lambda: list_vehicles(db_path)

            with (
                patch.object(db, "init_db", initialize_temp_database),
                patch.object(vehicles, "list_vehicles", read_temp_vehicles),
                patch.object(run_analysis, "list_vehicles", read_temp_vehicles),
            ):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run()
                self.assertEqual(len(app.exception), 0)
                self.assertGreaterEqual(len(app.title), 1)

                app.radio[0].set_value(app.radio[0].options[1]).run()
                self.assertEqual(len(app.exception), 0)

    def test_legacy_timestamp_utc_overflow_shows_page_error_without_crashing(self):
        with tempfile.TemporaryDirectory(prefix="rail-app-edge-time-") as directory:
            db_path = Path(directory) / "smoke.sqlite3"
            initialize_database = db.init_db
            initialize_temp_database = lambda: initialize_database(db_path)
            vehicle = {
                "id": 1,
                "vehicle_code": "DEMO-001",
                "vehicle_type": "测试车辆",
                "vehicle_length_m": 20,
                "mass_kg": 40000,
                "axle_count": 4,
                "bogie_count": 2,
                "max_speed_kmh": 120,
                "notes": "",
            }
            batches = [{
                "id": 1,
                "file_name": "legacy.csv",
                "imported_at": "2026-10-08 09:30:00",
                "sample_count": 1,
            }]
            samples = [{
                "timestamp": "0001-01-01T00:00:00+14:00",
                "speed_kmh": 80,
                "lateral_accel": 0.1,
                "vertical_accel": 0.1,
            }]

            with (
                patch.object(db, "init_db", initialize_temp_database),
                patch.object(vehicles, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicle_batches", return_value=batches),
                patch.object(run_analysis, "list_batch_samples", return_value=samples),
            ):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run()
                app.sidebar.radio[0].set_value(
                    app.sidebar.radio[0].options[1]
                ).run()

            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any(
                    "所选批次包含无效时间戳" in message.value
                    for message in app.error
                )
            )
    def test_dynamics_page_shows_sampling_summary_for_selected_batch(self):
        with tempfile.TemporaryDirectory(prefix="rail-app-sampling-") as directory:
            db_path = Path(directory) / "smoke.sqlite3"
            initialize_database = db.init_db
            initialize_temp_database = lambda: initialize_database(db_path)
            vehicle = {
                "id": 1,
                "vehicle_code": "DEMO-001",
                "vehicle_type": "测试车辆",
                "vehicle_length_m": 20,
                "mass_kg": 40000,
                "axle_count": 4,
                "bogie_count": 2,
                "max_speed_kmh": 120,
                "notes": "",
            }
            batches = [{
                "id": 1,
                "file_name": "demo.csv",
                "imported_at": "2026-10-08 09:30:00",
                "sample_count": 4,
            }]
            samples = [
                {
                    "timestamp": timestamp,
                    "speed_kmh": 80,
                    "lateral_accel": 0.1,
                    "vertical_accel": 0.1,
                }
                for timestamp in (
                    "2026-10-08T09:30:00Z",
                    "2026-10-08T09:30:01Z",
                    "2026-10-08T09:30:01Z",
                    "2026-10-08T09:30:03Z",
                )
            ]

            with (
                patch.object(db, "init_db", initialize_temp_database),
                patch.object(vehicles, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicle_batches", return_value=batches),
                patch.object(run_analysis, "list_batch_samples", return_value=samples),
            ):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run()
                app.sidebar.radio[0].set_value(
                    app.sidebar.radio[0].options[1]
                ).run()

            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any(item.value == "采样时间概览" for item in app.subheader))
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["重复时间戳记录"], "1")
            self.assertEqual(metrics["最小间隔（秒）"], "1.000")
            self.assertEqual(metrics["中位间隔（秒）"], "1.500")
            self.assertEqual(metrics["最大间隔（秒）"], "2.000")

    def test_comparison_page_renders_per_batch_sampling_summary(self):
        with tempfile.TemporaryDirectory(prefix="rail-app-comparison-sampling-") as directory:
            db_path = Path(directory) / "smoke.sqlite3"
            initialize_database = db.init_db
            initialize_temp_database = lambda: initialize_database(db_path)
            vehicle = {
                "id": 1,
                "vehicle_code": "DEMO-001",
                "vehicle_type": "测试车辆",
                "vehicle_length_m": 20,
                "mass_kg": 40000,
                "axle_count": 4,
                "bogie_count": 2,
                "max_speed_kmh": 120,
                "notes": "",
            }
            batches = [
                {
                    "id": batch_id,
                    "file_name": f"demo-{batch_id}.csv",
                    "imported_at": "2026-10-08 09:30:00",
                    "sample_count": count,
                }
                for batch_id, count in ((1, 4), (2, 2))
            ]
            samples_by_batch = {
                1: [
                    {
                        "timestamp": timestamp,
                        "speed_kmh": 80,
                        "lateral_accel": 0.1,
                        "vertical_accel": 0.1,
                    }
                    for timestamp in (
                        "2026-10-08T09:30:00Z",
                        "2026-10-08T09:30:01Z",
                        "2026-10-08T09:30:01Z",
                        "2026-10-08T09:30:03Z",
                    )
                ],
                2: [
                    {
                        "timestamp": timestamp,
                        "speed_kmh": 90,
                        "lateral_accel": 0.2,
                        "vertical_accel": 0.2,
                    }
                    for timestamp in (
                        "2026-10-09T09:30:00Z",
                        "2026-10-09T09:30:04Z",
                    )
                ],
            }

            with (
                patch.object(db, "init_db", initialize_temp_database),
                patch.object(vehicles, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicle_batches", return_value=batches),
                patch.object(
                    run_analysis,
                    "list_batch_samples",
                    side_effect=lambda _vehicle_id, batch_id: samples_by_batch[batch_id],
                ),
            ):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run()
                app.sidebar.radio[0].set_value(
                    app.sidebar.radio[0].options[1]
                ).run()

            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any(item.value == "批次采样时间概览" for item in app.subheader)
            )

    def test_import_page_shows_sampling_summary_before_database_import(self):
        with tempfile.TemporaryDirectory(prefix="rail-app-import-sampling-") as directory:
            db_path = Path(directory) / "smoke.sqlite3"
            initialize_database = db.init_db
            initialize_temp_database = lambda: initialize_database(db_path)
            vehicle = {
                "id": 1,
                "vehicle_code": "RV-001",
                "vehicle_type": "测试车辆",
                "vehicle_length_m": 20,
                "mass_kg": 40000,
                "axle_count": 4,
                "bogie_count": 2,
                "max_speed_kmh": 120,
                "notes": "",
            }
            csv_content = (
                "timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
                "2026-10-08T09:30:00Z,RV-001,80,0.1,0.1\n"
                "2026-10-08T09:30:00+00:00,RV-001,81,0.2,0.2\n"
                "2026-10-08T09:30:02Z,RV-001,82,0.3,0.3\n"
            ).encode("utf-8")

            with (
                patch.object(db, "init_db", initialize_temp_database),
                patch.object(vehicles, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicles", return_value=[vehicle]),
                patch.object(run_analysis, "list_vehicle_batches", return_value=[]),
                patch.object(run_analysis, "list_batch_samples", return_value=[]),
                patch.object(run_analysis, "list_import_batches", return_value=[]),
            ):
                app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run()
                app.sidebar.radio[0].set_value(
                    app.sidebar.radio[0].options[1]
                ).run()
                app.file_uploader[0].upload(
                    "sampling-demo.csv", csv_content, "text/csv"
                ).run()

            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any(
                    item.value == "采样时间概览（待导入数据）"
                    for item in app.subheader
                )
            )
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["重复时间戳记录"], "1")
            self.assertEqual(metrics["最小间隔（秒）"], "2.000")
            self.assertEqual(metrics["中位间隔（秒）"], "2.000")
            self.assertEqual(metrics["最大间隔（秒）"], "2.000")


if __name__ == "__main__":
    unittest.main()
