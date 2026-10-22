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


if __name__ == "__main__":
    unittest.main()
