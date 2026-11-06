"""Interactive cockpit checks using Streamlit's real local app runner."""

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import rail_vehicle.db as db
from rail_vehicle.pages import cockpit, vehicles
from rail_vehicle.replay import ReplayState
from rail_vehicle.demo_data import demo_csv_content
from rail_vehicle.run_data import import_run_data, validate_run_csv
from rail_vehicle.vehicle_data import add_vehicle


def cockpit_app():
    from rail_vehicle.pages.cockpit import render
    render()


class CockpitTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = self.stack.enter_context(patch.object(cockpit, "monotonic", return_value=100.0))
        self.stack.enter_context(patch.object(cockpit, "list_vehicles", return_value=[]))

    def demo_app(self):
        app = AppTest.from_function(cockpit_app).run()
        app.radio(key="cockpit_source").set_value("内置虚构演示").run()
        self.assertEqual(len(app.exception), 0)
        return app

    def test_response_schematic_uses_sample_direction_and_strict_threshold(self):
        sample = {"lateral_accel": -1.0, "vertical_accel": 1.1}
        chart = cockpit.build_response_schematic(sample, 1.0)
        lateral, vertical = chart.layout.annotations[1:]
        self.assertIn("← -1.000", lateral.text)
        self.assertEqual(lateral.font.color, "#58a6ff")
        self.assertIn("↑ +1.100", vertical.text)
        self.assertEqual(vertical.font.color, "#f46d7a")
        zero = cockpit.build_response_schematic({"lateral_accel": 0, "vertical_accel": 0}, 1)
        self.assertIn("●", zero.layout.annotations[1].text)
        self.assertIn("●", zero.layout.annotations[2].text)
        # Presentation may highlight channels, but cannot imply measured displacement.
        self.assertEqual(chart.to_plotly_json()["layout"]["shapes"],
                         zero.to_plotly_json()["layout"]["shapes"])

    def test_no_data_gives_guidance_and_demo_is_available(self):
        app = AppTest.from_function(cockpit_app).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("尚无车辆" in item.value for item in app.info))
        self.assertEqual(len(app.button), 0)
        app.radio[0].set_value("内置虚构演示").run()
        self.assertEqual(len(app.metric), 4)
        self.assertTrue(any("历史数据模拟回放" in item.value for item in app.info))

    def test_play_progress_anomaly_pause_and_reset(self):
        self.stack.enter_context(patch.object(cockpit, "build_response_schematic", wraps=cockpit.build_response_schematic))
        app = self.demo_app()
        first_speed = app.metric[0].value
        app.button[0].click().run()
        self.assertTrue(app.session_state[cockpit.STATE_KEY].playing)
        self.clock.return_value = 131.0
        app.run()
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 31)
        self.assertNotEqual(app.metric[0].value, first_speed)
        self.assertEqual(app.metric[1].value, "1.800")
        self.assertEqual(cockpit.build_response_schematic.call_args.args[0]["lateral_accel"], 1.8)
        self.assertTrue(any("横向加速度" in item.value for item in app.error))
        app.button[1].click().run()
        position = app.session_state[cockpit.STATE_KEY].position
        self.clock.return_value = 160.0
        app.run()
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, position)
        self.assertEqual(app.metric[3].value, "已暂停")
        app.button[2].click().run()
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 0)
        self.assertEqual(app.metric[0].value, first_speed)
        self.assertNotEqual(cockpit.build_response_schematic.call_args.args[0]["lateral_accel"], 1.8)
        self.assertEqual(len(app.exception), 0)

    def test_seek_event_window_return_and_threshold_invalidation(self):
        app = self.demo_app()
        app.slider(key="cockpit_seek").set_value(30.0).run()
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 30)
        self.assertEqual(app.metric[1].value, "1.400")
        app.button(key="cockpit_event_play").click().run()
        self.assertFalse(app.exception)

        window = app.session_state["cockpit_window"]["track"]
        self.assertEqual(len(window.samples), 13)
        self.assertEqual(window.duration, 12)
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 0)
        self.assertTrue(app.session_state[cockpit.STATE_KEY].playing)
        self.assertTrue(app.slider(key="cockpit_seek").disabled)
        next(button for button in app.button if button.label == "暂停").click().run()
        app.slider(key="cockpit_seek").set_value(5.0).run()
        self.assertEqual(app.metric[1].value, "1.400")
        app.button(key="cockpit_window_clear").click().run()
        self.assertNotIn("cockpit_window", app.session_state)
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 0)
        app.button(key="cockpit_event_play").click().run()
        app.number_input(key="cockpit_threshold").set_value(2.0).run()
        self.assertNotIn("cockpit_window", app.session_state)
        self.assertFalse(app.exception)

    def test_jump_to_event_restores_full_track_and_pauses_at_event_time(self):
        app = self.demo_app()
        app.selectbox(key="cockpit_rate").set_value(4.0).run()
        app.button(key="cockpit_event_play").click().run()
        self.assertTrue(app.session_state[cockpit.STATE_KEY].playing)
        app.selectbox(key="cockpit_event").set_value(1).run()
        app.button(key="cockpit_event_jump").click().run()
        self.assertFalse(app.exception)
        self.assertNotIn("cockpit_window", app.session_state)
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 70.0)
        self.assertEqual(app.session_state[cockpit.STATE_KEY].rate, 4.0)
        self.assertFalse(app.session_state[cockpit.STATE_KEY].playing)
        self.assertEqual(app.metric[2].value, "1.600")

    def test_duplicate_timestamp_jump_and_zero_duration_window_are_explicit(self):
        from rail_vehicle.replay import prepare_replay
        track = prepare_replay([
            dict(timestamp="2026-10-08T08:00:00Z", speed_kmh=60, lateral_accel=0, vertical_accel=0),
            dict(timestamp="2026-10-08T08:00:10Z", speed_kmh=61, lateral_accel=2, vertical_accel=0),
            dict(timestamp="2026-10-08T08:00:10Z", speed_kmh=62, lateral_accel=0, vertical_accel=0),
        ])
        with patch.object(cockpit, "_demo_track", return_value=track):
            app = self.demo_app()
            app.button(key="cockpit_event_jump").click().run()
            self.assertEqual(app.metric[0].value, "62.0")
            self.assertTrue(any("最后一条" in item.value for item in app.caption))
            app.number_input(key="cockpit_margin").set_value(0.0).run()
            app.button(key="cockpit_event_play").click().run()
            self.assertFalse(app.session_state[cockpit.STATE_KEY].playing)
            self.assertEqual(len(app.session_state["cockpit_window"]["track"].samples), 2)
            self.assertTrue(any("无可播放的时间跨度" in item.value for item in app.info))
            self.assertFalse(app.exception)

    def test_chart_shows_samples_without_lines_across_gaps(self):
        from rail_vehicle.replay import prepare_replay
        track = prepare_replay([
            dict(timestamp="2026-10-08T08:00:00Z", speed_kmh=60, lateral_accel=2, vertical_accel=0),
            dict(timestamp="2026-10-08T08:00:30Z", speed_kmh=70, lateral_accel=2, vertical_accel=0),
        ])
        chart = cockpit.build_replay_chart(track, 1, 30, 1)
        self.assertTrue(all(trace.mode == "markers" for trace in chart.data))
        self.assertEqual(list(chart.data[0].x), [0, 30])
        self.assertTrue(any(shape.type == "rect" for shape in chart.layout.shapes))

    def test_rate_threshold_end_and_source_reset(self):
        app = self.demo_app()
        app.selectbox(key="cockpit_rate").set_value(4.0).run()
        app.button[0].click().run()
        self.clock.return_value = 108.0
        app.run()
        self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 32)
        app.number_input(key="cockpit_threshold").set_value(2.0).run()
        self.assertEqual(len(app.error), 0)
        self.clock.return_value = 140.0
        app.run()
        self.assertFalse(app.session_state[cockpit.STATE_KEY].playing)
        self.assertEqual(app.metric[3].value, "回放结束")
        app.radio[0].set_value("历史运行批次").run()
        self.assertNotIn(cockpit.STATE_KEY, app.session_state)

    def test_historical_batches_select_and_reset_position(self):
        vehicle = dict(id=1, vehicle_code="DEMO-001", max_speed_kmh=120)
        batches = [dict(id=i, file_name=f"fictional-{i}.csv", sample_count=2) for i in (1, 2)]
        samples = [dict(timestamp=f"2026-10-08T08:00:{s:02d}Z", speed_kmh=60+s,
                        lateral_accel=0.1, vertical_accel=0.2) for s in (0, 10)]
        with patch.object(cockpit, "list_vehicles", return_value=[vehicle]), \
             patch.object(cockpit, "list_vehicle_batches", return_value=batches), \
             patch.object(cockpit, "list_batch_samples", return_value=samples) as load:
            app = AppTest.from_function(cockpit_app).run()
            app.button[0].click().run()
            self.clock.return_value = 105
            app.run()
            self.assertEqual(app.session_state[cockpit.STATE_KEY].position, 5)
            app.selectbox(key="cockpit_batch_1").set_value(2).run()
            self.assertEqual(app.session_state[cockpit.STATE_KEY], ReplayState())
            self.assertEqual(load.call_count, 2)  # Timer renders never reload the database.
            self.assertEqual(len(app.exception), 0)

    def test_empty_invalid_and_zero_duration_batches_do_not_crash(self):
        vehicle = dict(id=1, vehicle_code="DEMO-001", max_speed_kmh=120)
        batches = [dict(id=1, file_name="fictional.csv", sample_count=1)]
        for samples in ([], [dict(timestamp="bad", speed_kmh=60, lateral_accel=0, vertical_accel=0)]):
            with self.subTest(samples=samples), \
                 patch.object(cockpit, "list_vehicles", return_value=[vehicle]), \
                 patch.object(cockpit, "list_vehicle_batches", return_value=batches), \
                 patch.object(cockpit, "list_batch_samples", return_value=samples):
                app = AppTest.from_function(cockpit_app).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(app.error), 1)
        samples = [dict(timestamp="2026-10-08T08:00:00Z", speed_kmh=60, lateral_accel=0, vertical_accel=0)]
        with patch.object(cockpit, "list_vehicles", return_value=[vehicle]), \
             patch.object(cockpit, "list_vehicle_batches", return_value=batches), \
             patch.object(cockpit, "list_batch_samples", return_value=samples):
            app = AppTest.from_function(cockpit_app).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.button[0].disabled)

    def test_main_navigation_opens_cockpit_and_leaving_stops_playback(self):
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        initialize = db.init_db
        self.stack.enter_context(patch.object(db, "init_db", lambda: initialize(Path(directory) / "app.sqlite3")))
        self.stack.enter_context(patch.object(vehicles, "list_vehicles", return_value=[]))
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
        app.button(key="nav_动态运行驾驶舱").click().run()
        app.radio(key="cockpit_source").set_value("内置虚构演示").run()
        app.button[0].click().run()
        app.button(key="nav_车辆参数").click().run()
        self.assertNotIn(cockpit.STATE_KEY, app.session_state)
        self.assertEqual(len(app.exception), 0)

    def test_imported_sqlite_samples_can_be_selected_and_replayed(self):
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        path = Path(directory) / "history.sqlite3"
        db.init_db(path)
        vehicle_id = add_vehicle(dict(
            vehicle_code="DEMO-001", vehicle_type="虚构演示车辆", vehicle_length_m=20,
            mass_kg=40000, axle_count=4, bogie_count=2, max_speed_kmh=120,
        ), path)
        registered = [dict(id=vehicle_id, vehicle_code="DEMO-001", max_speed_kmh=120)]
        batch_id = import_run_data("fictional.csv", validate_run_csv(demo_csv_content(0), registered), path)
        read_batches, read_samples = cockpit.list_vehicle_batches, cockpit.list_batch_samples
        with patch.object(cockpit, "list_vehicles", return_value=registered), \
             patch.object(cockpit, "list_vehicle_batches", lambda vid: read_batches(vid, path)), \
             patch.object(cockpit, "list_batch_samples", lambda vid, bid: read_samples(vid, bid, path)):
            app = AppTest.from_function(cockpit_app).run()
            self.assertEqual(app.session_state[cockpit.SELECTION_KEY], (vehicle_id, batch_id))
            app.button[0].click().run()
            self.clock.return_value = 171
            app.run()
            self.assertEqual(app.metric[2].value, "-1.700")
            self.assertTrue(any("垂向加速度" in message.value for message in app.error))
            self.assertEqual(len(app.exception), 0)

    def test_chart_shows_no_future_samples_and_keeps_observed_anomaly_markers(self):
        from rail_vehicle.replay import prepare_replay
        samples = [dict(timestamp=f"2026-10-08T08:00:{s:02d}Z", speed_kmh=60+s,
                        lateral_accel=2 if s == 1 else 0, vertical_accel=0) for s in range(5)]
        track = prepare_replay(samples)
        chart = cockpit.build_replay_chart(track, 2, 2, 1)
        self.assertEqual(list(chart.data[0].x), [0, 1, 2])
        self.assertEqual(list(chart.data[0].y), [60, 61, 62])
        self.assertEqual(list(chart.data[2].x), [1])
        self.assertEqual(list(chart.data[2].y), [2])
