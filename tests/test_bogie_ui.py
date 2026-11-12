import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from rail_vehicle.db import init_db
from rail_vehicle.run_data import import_run_data, list_batch_samples, list_vehicle_batches, validate_run_csv
from rail_vehicle.vehicle_data import add_vehicle, list_vehicles


class BogiePageTests(unittest.TestCase):
    def test_csv_batch_evidence_link_jumps_to_verified_sample(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "test.db"
            init_db(db)
            vehicle = add_vehicle(dict(vehicle_code="TEST-BOGIE", vehicle_type="虚构测试车辆",
                                       vehicle_length_m=20, mass_kg=40000, axle_count=4, bogie_count=2,
                                       max_speed_kmh=120, notes=""), db)
            csv = ("timestamp,vehicle_id,speed_kmh,lateral_accel,vertical_accel\n"
                   "2026-01-01T00:00:00Z,TEST-BOGIE,20,0,0\n"
                   "2026-01-01T00:00:01Z,TEST-BOGIE,35,-2,2\n")
            batch = import_run_data("fictional.csv", validate_run_csv(csv, list_vehicles(db)), db)
            with (patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "false"}),
                  patch("rail_vehicle.pages.bogie.list_vehicles", side_effect=lambda: list_vehicles(db)),
                  patch("rail_vehicle.pages.bogie.list_vehicle_batches",
                        side_effect=lambda vid: list_vehicle_batches(vid, db)),
                  patch("rail_vehicle.pages.bogie.list_batch_samples",
                        side_effect=lambda vid, bid: list_batch_samples(vid, bid, db))):
                app = AppTest.from_string("from rail_vehicle.pages.bogie import render; render()")
                app.session_state["bogie_evidence_target"] = dict(
                    vehicle_id=vehicle, batch_id=batch, threshold=1.,
                    timestamp="2026-01-01T00:00:01Z", axis="lateral")
                app.run()
                self.assertFalse(app.exception)
                self.assertTrue(any("已定位" in entry.value for entry in app.success))
                self.assertEqual(app.metric[0].value, "35.0")
                self.assertTrue(any(entry.value == "综合振动异常" for entry in app.warning))

    def test_teaching_controls_and_demo_event_jump(self):
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}):
            app = AppTest.from_string("from rail_vehicle.pages.bogie import render; render()").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("component_instance")), 1)
            app.selectbox(key="bogie_teaching").set_value("轴承异常示意").run()
            app.checkbox[0].check().run()
            app.toggle(key="bogie_labels").set_value(True).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("component_instance")), 2)
            app.selectbox(key="bogie_renderer").set_value("Plotly 兼容模式").run()
            self.assertEqual(len(app.get("plotly_chart")), 2)
            app.radio(key="bogie_source").set_value("内置虚构演示").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("plotly_chart")), 2)
            next(button for button in app.button if button.label == "定位区段起点").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("超阈值" in warning.value or "综合振动异常" in warning.value
                                for warning in app.warning))
            next(button for button in app.button if button.label == "下一帧").click().run()
            self.assertFalse(app.exception)
            app.selectbox(key="bogie_renderer").set_value("Three.js 工作台").run()
            self.assertGreaterEqual(len(app.get("component_instance")), 1)
            self.assertEqual(len(app.get("plotly_chart")), 1)  # synchronized signals


if __name__ == "__main__":
    unittest.main()
