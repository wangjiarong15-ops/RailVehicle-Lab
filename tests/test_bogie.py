import math
import unittest

from rail_vehicle.bogie import build_bogie, motion
from rail_vehicle.bogie_geometry import AXLE_X, AXLE_Z, WHEEL_POSE_GAIN
from rail_vehicle.bogie_replay import (
    COMBINED, GAP, LATERAL, NORMAL, VERTICAL, advance_event_loop, control_event_loop, event_loop,
    event_positions, gap_cutoff, replay_scene, scene_for_sample,
)
from rail_vehicle.replay import ReplayState, prepare_replay


class BogieTests(unittest.TestCase):
    def test_motion_is_prescribed_and_deterministic(self):
        self.assertEqual(motion("正常运行", 1), (0, 0, 0))
        self.assertEqual(motion("垂向振动", math.pi / 2), (0, .065, 0))
        self.assertEqual(motion("横向振动", math.pi / 2), (.075, 0, 0))
        self.assertEqual(motion("蛇行运动示意", 0), (0, 0, .049))

    def test_visibility_and_highlight_include_solid_wheels(self):
        fig = build_bogie(visible=["轮对"], highlight="轮对")
        self.assertTrue(fig.data)
        self.assertTrue(all(trace.name == "轮对" for trace in fig.data))
        self.assertEqual(sum(trace.type == "mesh3d" for trace in fig.data), 6)
        self.assertTrue(all((trace.color if trace.type == "mesh3d" else trace.line.color) == "#ffca45"
                            for trace in fig.data))

    def test_wheel_markers_show_forward_rolling_in_plotly(self):
        def marks(mode, phase):
            trace = build_bogie(mode, phase, visible=["轮对"]).data[-1]
            return tuple(zip(trace.x, trace.y, trace.z))[1::3]  # Outer endpoint on each wheel.

        start = marks("正常运行", 0.)
        quarter_turn = math.pi / 2
        rolling = marks("正常运行", quarter_turn)
        # The forward witness point descends during a positive quarter turn about Y.
        for axle_x, (start_x, start_y, start_z), (x, y, z) in zip(
                (AXLE_X[0], AXLE_X[0], AXLE_X[1], AXLE_X[1]), start, rolling, strict=True):
            self.assertGreater(start_x, axle_x)
            self.assertAlmostEqual(start_z, AXLE_Z)
            self.assertAlmostEqual(x, axle_x)
            self.assertAlmostEqual(y, start_y)
            self.assertAlmostEqual(AXLE_Z - z, start_x - axle_x)

        # Yaw follows the wheelset after spin and doesn't change marker height.
        yaw_phase = quarter_turn / 2
        lateral, _, yaw = motion("蛇行运动示意", yaw_phase)
        snake = marks("蛇行运动示意", yaw_phase)
        normal = marks("正常运行", yaw_phase)
        cosine, sine = math.cos(WHEEL_POSE_GAIN * yaw), math.sin(WHEEL_POSE_GAIN * yaw)
        for (x, y, z), (snake_x, snake_y, snake_z) in zip(normal, snake, strict=True):
            self.assertAlmostEqual(snake_x, x * cosine - y * sine)
            self.assertAlmostEqual(snake_y, x * sine + y * cosine + WHEEL_POSE_GAIN * lateral)
            self.assertAlmostEqual(snake_z, z)

    def test_orthographic_view_labels_only_visible_parts(self):
        figure = build_bogie(visible=["构架", "轴箱"], labels=True, view="俯视图")
        self.assertEqual(figure.layout.scene.camera.projection.type, "orthographic")
        self.assertEqual(figure.data[-1].mode, "markers+text")
        self.assertEqual(tuple(figure.data[-1].text), ("构架", "轴箱"))
        self.assertFalse(build_bogie(visible=[], labels=True).data)

    def test_threshold_uses_signed_magnitude_and_same_sample(self):
        for lateral, vertical, expected in [(1, -1, NORMAL), (-2, 0, LATERAL),
                                             (0, 2, VERTICAL), (-2, -3, COMBINED)]:
            self.assertEqual(scene_for_sample({"lateral_accel": lateral, "vertical_accel": vertical}, 1),
                             expected)

    def test_event_jump_preserves_distinct_runs_at_the_same_timestamp(self):
        track = prepare_replay([dict(timestamp="2026-01-01T00:00:00Z", speed_kmh=2,
                                     lateral_accel=value, vertical_accel=0) for value in (2, 0, 3)])
        self.assertEqual([index for _, index in event_positions(track, 1)], [0, 2])

    def test_loop_excludes_observations_outside_requested_margins(self):
        track = prepare_replay([dict(timestamp=f"2026-01-01T00:00:{second:02d}Z", speed_kmh=2,
                                     lateral_accel=2 if second in (3, 7) else 0, vertical_accel=0)
                                for second in (0, 1, 3, 7, 10)])
        [(event, first)] = event_positions(track, 1)
        window = event_loop(track, event, first, 1.5, 1.5)
        self.assertTrue(window.timed)
        self.assertEqual((window.start, window.end, window.first_row, window.last_row), (3, 7, 2, 3))
        self.assertEqual((window.index_at(track, 1.5), window.index_at(track, 9)), (2, 3))
        state = ReplayState(position=3, playing=True, last_tick=10.)
        end = advance_event_loop(state, 15., window)
        self.assertEqual(end.position, 7.)
        self.assertEqual(advance_event_loop(end, 16., window).position, 3.)

    def test_equal_time_loop_keeps_exact_rows_without_advancing_time(self):
        track = prepare_replay([dict(timestamp=f"2026-01-01T00:00:{second:02d}Z", speed_kmh=2,
                                     lateral_accel=value, vertical_accel=0)
                                for second, value in ((0, 0), (2, 0), (2, -3), (2, -4), (2, 0), (4, 0))])
        [(event, first)] = event_positions(track, 1)
        window = event_loop(track, event, first, 0, 0)
        self.assertFalse(window.timed)
        self.assertEqual((window.start, window.end, window.first_row, window.last_row), (2, 2, 2, 3))
        self.assertEqual(window.index_at(track, 2), 2)
        state = ReplayState(position=2., playing=True, last_tick=10.)
        expected = ReplayState(position=2.)
        self.assertEqual(advance_event_loop(state, 15., window), expected)
        self.assertEqual(control_event_loop(expected, "play", 15., window), expected)

    def test_long_gap_does_not_extend_anomaly_scene(self):
        track = prepare_replay([dict(timestamp=f"2026-01-01T00:00:{second:02d}Z", speed_kmh=2,
                                     lateral_accel=2 if second == 2 else 0, vertical_accel=0)
                                for second in (0, 1, 2, 15, 16)])
        cutoff = gap_cutoff(track)
        self.assertEqual(cutoff, 3)
        self.assertIs(replay_scene(track, 2, 2, 1, cutoff), LATERAL)
        self.assertIs(replay_scene(track, 5, 2, 1, cutoff), GAP)
        self.assertIs(replay_scene(track, 15, 3, 1, cutoff), NORMAL)


if __name__ == "__main__":
    unittest.main()
