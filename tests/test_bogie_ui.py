import csv
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from rail_vehicle.db import init_db
from rail_vehicle.pages.bogie import EVIDENCE_KEY, FRAME_KEY
from rail_vehicle.run_data import import_run_data, list_batch_samples, list_vehicle_batches, validate_run_csv
from rail_vehicle.vehicle_data import add_vehicle, list_vehicles


class BogiePageTests(unittest.TestCase):
    def test_same_time_review_steps_and_exports_only_selected_rows(self):
        observations = [(0, 20, 0), (1, 30, 2), (1, 40, 0), (1, 50, -3),
                        (1, 60, -4), (1, 70, 0), (2, 80, 0)]
        rows = [dict(timestamp=f"2026-01-01T00:00:{second:02d}Z", speed_kmh=speed,
                     lateral_accel=lateral, vertical_accel=0) for second, speed, lateral in observations]
        with (patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}),
              patch("rail_vehicle.pages.bogie.analysis_demo_data", return_value=(None, None, {-1: rows}))):
            app = AppTest.from_string("from rail_vehicle.pages.bogie import render; render()").run()
            app.radio(key="bogie_source").set_value("内置虚构演示").run()
            app.selectbox(key="bogie_event_choice_axes").set_value(1).run()
            app.number_input(key="bogie_loop_before").set_value(0.)
            app.number_input(key="bogie_loop_after").set_value(0.).run()
            start = app.button(key="bogie_start_loop")
            self.assertEqual(start.label, "逐帧核查此区段")
            start.click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state[FRAME_KEY], 3)
            self.assertEqual(app.metric[0].value, "50.0")
            self.assertTrue(app.button(key="bogie_play").disabled)
            self.assertFalse(any(slider.key == "bogie_seek" for slider in app.slider))

            next(button for button in app.button if button.label == "下一帧").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state[FRAME_KEY], 4)
            self.assertEqual(app.metric[0].value, "60.0")
            self.assertTrue(next(button for button in app.button if button.label == "下一帧").disabled)
            chart = json.loads(app.get("plotly_chart")[0].proto.spec)
            self.assertEqual(chart["data"][0]["x"], [4, 5])
            self.assertEqual(chart["layout"]["xaxis3"]["title"]["text"], "批次样本序号（同一 UTC 时刻）")

            app.button(key="bogie_prepare_window_csv").click().run()
            export = list(csv.DictReader(io.StringIO(app.session_state[EVIDENCE_KEY].decode("utf-8-sig"))))
            self.assertEqual([(row["batch_row"], row["speed_kmh"]) for row in export], [("4", "50.0"), ("5", "60.0")])
            self.assertEqual(len({row["timestamp_utc"] for row in export}), 1)
            app.button(key="bogie_exit_loop").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state[FRAME_KEY], 4)
            self.assertEqual(app.metric[0].value, "60.0")

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
            app.toggle(key="bogie_compare").set_value(True).run()
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

    def test_related_review_preserves_observation_and_exits_on_next_sample(self):
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}):
            app = AppTest.from_string("from rail_vehicle.pages.bogie import render; render()").run()
            action = "bogie_related_review_action"
            self.assertFalse(any(button.key == action for button in app.button))

            app.radio(key="bogie_source").set_value("内置虚构演示").run()
            self.assertFalse(any(button.key == action for button in app.button))
            app.selectbox(key="bogie_view").set_value("俯视图").run()
            app.multiselect(key="bogie_visible_parts").set_value(["轮对"]).run()
            next(button for button in app.button if button.label == "定位区段起点").click().run()

            def model():
                self.assertFalse(app.exception)
                return json.loads(app.get("component_instance")[0].proto.json_args)

            baseline = model()
            self.assertEqual(baseline["exportContext"]["sample_number"], 31)
            self.assertEqual(app.button(key=action).label, "查看关联构件")
            app.button(key=action).click().run()
            reviewed = model()
            self.assertEqual(app.button(key=action).label, "返回原检查视图")
            self.assertEqual(reviewed["relatedReview"], ["构架", "一系悬挂"])
            self.assertEqual(reviewed["highlight"], baseline["highlight"])
            self.assertEqual(reviewed["exportContext"], baseline["exportContext"])
            self.assertEqual(reviewed["phase"], baseline["phase"])
            self.assertEqual(app.selectbox(key="bogie_view").value, "俯视图")
            self.assertEqual(app.multiselect(key="bogie_visible_parts").value, ["轮对"])

            app.button(key=action).click().run()
            self.assertEqual(model(), baseline)
            app.button(key=action).click().run()
            next(button for button in app.button if button.label == "下一帧").click().run()
            next_frame = model()
            self.assertEqual(next_frame["exportContext"]["sample_number"], 32)
            self.assertEqual(next_frame["relatedReview"], [])
            self.assertEqual(next_frame["visible"], ["轮对"])
            self.assertEqual(next_frame["view"], "俯视图")
            self.assertEqual(app.button(key=action).label, "查看关联构件")

            app.selectbox(key="bogie_renderer").set_value("Plotly 兼容模式").run()
            self.assertFalse(app.exception)
            self.assertFalse(any(button.key == action for button in app.button))

    def test_part_filter_survives_leaving_the_view(self):
        with patch.dict(os.environ, {"RAIL_PUBLIC_DEMO": "true"}):
            app = AppTest.from_string(
                "import streamlit as st\n"
                "from rail_vehicle.pages.bogie import render\n"
                "if st.checkbox('查看模型', value=True):\n"
                "    render()\n"
            ).run()
            for chosen in (["轮对"], []):
                app.multiselect(key="bogie_visible_parts").set_value(chosen).run()
                app.checkbox[0].set_value(False).run()
                self.assertFalse(app.multiselect)
                app.checkbox[0].set_value(True).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.multiselect(key="bogie_visible_parts").value, chosen)


if __name__ == "__main__":
    unittest.main()
