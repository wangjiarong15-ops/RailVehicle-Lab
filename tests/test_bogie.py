import math
import unittest

from rail_vehicle.bogie import build_bogie, motion
from rail_vehicle.bogie_replay import (
    COMBINED, GAP, LATERAL, NORMAL, VERTICAL, event_positions, gap_cutoff, replay_scene, scene_for_sample,
)
from rail_vehicle.replay import prepare_replay


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
