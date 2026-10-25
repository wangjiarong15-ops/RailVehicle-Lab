"""Known-signal checks for unweighted vibration analysis and sampling gates."""

import builtins
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import math
import unittest
from unittest.mock import patch

from rail_vehicle.demo_data import vibration_demo_samples
from rail_vehicle.vibration import (
    VibrationUnavailableError, analyze_vibration, signal_libraries, vibration_fingerprint,
)


def tone_samples(frequency=2, amplitude=0.4, bias=0.3, rate=100, count=2000):
    start = datetime(2026, 10, 8, tzinfo=timezone.utc)
    return [dict(timestamp=(start + timedelta(seconds=i / rate)).isoformat(), speed_kmh=72,
                 lateral_accel=bias + amplitude * math.sin(2 * math.pi * frequency * i / rate),
                 vertical_accel=0.0) for i in range(count)]


@unittest.skipUnless(importlib.util.find_spec("scipy"), "Install optional .[signal] dependencies")
class VibrationAnalysisTests(unittest.TestCase):
    def test_known_tone_rms_crest_peak_and_psd_band_power(self):
        result = analyze_vibration(tone_samples())
        channel = result["channels"]["lateral_accel"]
        self.assertTrue(result["spectral_available"])
        self.assertAlmostEqual(result["sampling_rate_hz"], 100)
        self.assertAlmostEqual(channel["raw_mean"], 0.3)
        self.assertAlmostEqual(channel["raw_rms"], math.sqrt(0.3**2 + 0.4**2 / 2))
        self.assertAlmostEqual(channel["dynamic_rms"], 0.4 / math.sqrt(2))
        self.assertAlmostEqual(channel["crest_factor"], math.sqrt(2), delta=0.005)
        self.assertAlmostEqual(channel["dominant_frequency_hz"], 2, delta=result["frequency_resolution_hz"])
        self.assertAlmostEqual(channel["band_rms"], channel["dynamic_rms"], delta=0.001)

    def test_frequency_band_excludes_tone_and_limits_returned_psd(self):
        result = analyze_vibration(tone_samples(), band_low=5, band_high=10)
        channel = result["channels"]["lateral_accel"]
        self.assertLess(channel["band_rms"], 0.001)
        self.assertTrue(all(5 <= frequency <= 10 for frequency in channel["frequencies"]))

    def test_high_sampling_rate_uses_segments_long_enough_for_low_band(self):
        result = analyze_vibration(tone_samples(frequency=1.5, rate=500, count=10000))
        self.assertTrue(result["spectral_available"])
        self.assertGreaterEqual(result["segment_samples"] / result["sampling_rate_hz"], 5 / result["band_low"])
        self.assertLessEqual(result["frequency_resolution_hz"], result["band_low"] / 5)
        self.assertAlmostEqual(result["channels"]["lateral_accel"]["dominant_frequency_hz"], 1.5, delta=0.1)

    def test_constant_signal_has_no_dynamic_peak_or_undefined_nan(self):
        samples = tone_samples(amplitude=0, bias=0.125)
        channel = analyze_vibration(samples)["channels"]["lateral_accel"]
        self.assertEqual(channel["dynamic_rms"], 0)
        self.assertIsNone(channel["crest_factor"])
        self.assertIsNone(channel["dominant_frequency_hz"])
        self.assertEqual(channel["band_rms"], 0)

    def test_demo_peaks_and_rolling_increase_match_injected_signal(self):
        samples = vibration_demo_samples()
        original = deepcopy(samples)
        result = analyze_vibration(samples)
        self.assertEqual(samples, original)
        self.assertEqual(result["sample_count"], 3000)
        for field, expected_frequency in (("lateral_accel", 1.5), ("vertical_accel", 6)):
            channel = result["channels"][field]
            self.assertAlmostEqual(channel["dominant_frequency_hz"], expected_frequency, delta=0.1)
            outside = [value for time, value in zip(channel["rolling_end_seconds"], channel["rolling_rms"]) if 5 < time < 10]
            inside = [value for time, value in zip(channel["rolling_end_seconds"], channel["rolling_rms"]) if 19 < time < 20]
            self.assertGreater(min(inside), 2 * max(outside))

    def test_low_sampling_rate_blocks_high_band_but_preserves_time_statistics(self):
        samples = tone_samples(rate=1, count=120, frequency=0.1)
        result = analyze_vibration(samples)
        self.assertFalse(result["spectral_available"])
        self.assertTrue(any("奈奎斯特" in issue for issue in result["issues"]))
        self.assertGreater(result["channels"]["lateral_accel"]["raw_rms"], 0)
        self.assertEqual(result["channels"]["lateral_accel"]["psd"], [])
        self.assertTrue(analyze_vibration(samples, band_low=0.05, band_high=0.4)["spectral_available"])

    def test_duplicates_and_large_jitter_are_not_silently_fixed(self):
        samples = tone_samples()
        samples[1]["timestamp"] = samples[0]["timestamp"]
        result = analyze_vibration(samples)
        self.assertFalse(result["spectral_available"])
        self.assertEqual(result["sample_count"], 2000)
        self.assertEqual(result["sampling"]["duplicate_timestamp_rows"], 1)
        samples = tone_samples()
        instant = datetime.fromisoformat(samples[100]["timestamp"])
        samples[100]["timestamp"] = (instant + timedelta(milliseconds=2)).isoformat()
        result = analyze_vibration(samples)
        self.assertFalse(result["spectral_available"])
        self.assertTrue(any("1%" in issue for issue in result["issues"]))

    def test_too_few_short_and_single_instant_records_have_no_spectrum(self):
        for samples in (tone_samples(count=30), tone_samples(count=100), [tone_samples()[0]]):
            with self.subTest(count=len(samples)):
                result = analyze_vibration(samples)
                self.assertFalse(result["spectral_available"])
                self.assertTrue(result["issues"])
                self.assertIsNotNone(result["channels"]["lateral_accel"]["raw_rms"])

    def test_invalid_inputs_and_bounds_are_rejected(self):
        for samples in ([], [dict(timestamp="bad", speed_kmh=1, lateral_accel=0, vertical_accel=0)],
                        [dict(timestamp="2026-10-08T00:00:00Z", speed_kmh=1, lateral_accel=float("nan"), vertical_accel=0)],
                        [dict(timestamp="2026-10-08T00:00:00Z", speed_kmh=1, lateral_accel=101, vertical_accel=0)]):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                analyze_vibration(samples)
        for parameters in (dict(band_low=0), dict(band_high=0.4), dict(window_seconds=-1), dict(band_low=float("nan"))):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                analyze_vibration(tone_samples(), **parameters)
        with patch("rail_vehicle.vibration.MAX_SAMPLES", 10), self.assertRaises(ValueError):
            analyze_vibration(tone_samples(count=20))

    def test_window_bounds_do_not_produce_invalid_curves_or_overflow(self):
        for window in (0.001, 100, 1e308):
            with self.subTest(window=window):
                result = analyze_vibration(tone_samples(), window_seconds=window)
                self.assertEqual(result["channels"]["lateral_accel"]["rolling_rms"], [])

    def test_utc_offsets_sort_consistently_and_fingerprint_tracks_parameters(self):
        samples = tone_samples()
        samples[0]["timestamp"] = "2026-10-08T08:00:00+08:00"
        result = analyze_vibration(list(reversed(samples)))
        self.assertEqual(result["offsets"][0], 0)
        self.assertTrue(result["spectral_available"])
        params = dict(band_low=0.5, band_high=20, window_seconds=1)
        fingerprint = vibration_fingerprint(samples, dict(batch=1), **params)
        for key, value in (("band_low", 1), ("band_high", 15), ("window_seconds", 2)):
            self.assertNotEqual(fingerprint, vibration_fingerprint(samples, dict(batch=1), **{**params, key: value}))
        self.assertNotEqual(fingerprint, vibration_fingerprint(samples[:1000], dict(batch=1), **params))


class ScientificDependencyTests(unittest.TestCase):
    def test_missing_dependency_message_does_not_leak_import_details(self):
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name == "scipy.signal":
                raise ImportError("PRIVATE_DEPENDENCY_DETAIL")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaises(VibrationUnavailableError) as raised:
                signal_libraries()
        self.assertNotIn("PRIVATE_DEPENDENCY_DETAIL", str(raised.exception))
