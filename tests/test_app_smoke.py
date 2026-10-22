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


if __name__ == "__main__":
    unittest.main()
