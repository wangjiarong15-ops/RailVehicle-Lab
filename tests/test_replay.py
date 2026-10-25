"""Historical replay timing, thresholds and data handling without wall-clock sleeps."""

import unittest

from rail_vehicle.replay import (
    ReplayState, advance_replay, control_replay, prepare_replay, sample_status,
)


def row(timestamp, speed=60, lateral=0.1, vertical=-0.2):
    return dict(timestamp=timestamp, speed_kmh=speed,
                lateral_accel=lateral, vertical_accel=vertical)


class ReplayTests(unittest.TestCase):
    def test_track_orders_utc_and_preserves_irregular_spacing_and_values(self):
        samples = [row("2026-10-08T08:00:10Z", 80),
                   row("2026-10-08T16:00:00+08:00", 40),
                   row("2026-10-08T08:00:02Z", 60)]
        track = prepare_replay(samples)
        self.assertEqual(track.offsets, (0, 2, 10))
        self.assertEqual(track.samples[0]["speed_kmh"], 40)
        self.assertEqual(track.index_at(7), 1)  # Hold the last observed sample; no interpolation.
        self.assertEqual(track.index_at(10), 2)
        self.assertEqual(samples[0]["speed_kmh"], 80)

    def test_pause_resume_and_rate_changes_preserve_elapsed_time(self):
        state = control_replay(ReplayState(), "play", 10, 100)
        state = control_replay(state, "rate", 12, 100, rate=4)
        self.assertEqual(state.position, 2)
        state = control_replay(state, "pause", 14, 100)
        self.assertEqual(state.position, 10)
        self.assertFalse(state.playing)
        self.assertEqual(advance_replay(state, 90, 100), state)
        state = control_replay(state, "play", 100, 100)
        self.assertEqual(advance_replay(state, 101, 100).position, 14)

    def test_end_stops_and_play_again_restarts(self):
        state = control_replay(ReplayState(), "play", 0, 3)
        state = advance_replay(state, 5, 3)
        self.assertEqual(state.position, 3)
        self.assertFalse(state.playing)
        state = control_replay(state, "play", 6, 3)
        self.assertEqual(state.position, 0)
        self.assertTrue(state.playing)

    def test_reset_preserves_rate_and_clears_time(self):
        state = ReplayState(position=20, playing=True, rate=2, last_tick=5)
        self.assertEqual(control_replay(state, "reset", 6, 100), ReplayState(rate=2))

    def test_single_timestamp_and_duplicates_are_displayable_without_playing(self):
        track = prepare_replay([row("2026-10-08T08:00:00Z", 40),
                                row("2026-10-08T08:00:00Z", 50)])
        self.assertEqual(track.duration, 0)
        self.assertEqual(track.index_at(0), 1)
        self.assertFalse(control_replay(ReplayState(), "play", 1, 0).playing)

    def test_status_uses_strict_absolute_threshold_on_both_axes(self):
        self.assertEqual(sample_status(row("", lateral=-1, vertical=1), 1), ())
        self.assertEqual(sample_status(row("", lateral=-2, vertical=2), 1),
                         ("横向加速度", "垂向加速度"))
        self.assertEqual(sample_status(row("", vertical=-2), 1), ("垂向加速度",))

    def test_invalid_data_and_controls_are_rejected(self):
        for samples in ([], [row("bad")], [row("2026-10-08T08:00:00Z", lateral=float("nan"))]):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                prepare_replay(samples)
        for rate in (0, -1, float("nan"), float("inf")):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                control_replay(ReplayState(), "rate", 0, 100, rate=rate)

    def test_fractional_rate_and_backward_clock_do_not_reverse_replay(self):
        state = ReplayState(playing=True, rate=0.5, last_tick=10)
        self.assertEqual(advance_replay(state, 12, 100).position, 1)
        self.assertEqual(advance_replay(state, 9, 100).position, 0)
