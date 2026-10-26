"""Deterministic experiment PDFs and session-bound export regression checks."""

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.demo_data import ml_demo_samples, vibration_demo_samples
from rail_vehicle.experiment_report import build_ml_report, build_vibration_report, generate_experiment_pdf
from rail_vehicle.ml_anomaly import detect_ml_anomalies
from rail_vehicle.pages import experiment_export, ml_detection, vibration_analysis
from rail_vehicle.vibration import analyze_vibration


FINGERPRINT = "a" * 64
NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
HAS_ML = bool(importlib.util.find_spec("sklearn"))
HAS_SIGNAL = bool(importlib.util.find_spec("scipy"))


def ml_app():
    from rail_vehicle.pages.ml_detection import render
    render()


def vibration_app():
    from rail_vehicle.pages.vibration_analysis import render
    render()


@unittest.skipUnless(HAS_ML, "Install optional .[ml] dependencies")
class MLReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = detect_ml_anomalies(*ml_demo_samples())

    def report(self):
        return build_ml_report(self.result, FINGERPRINT, dict(source="demo"), generated_at=NOW)

    def test_report_copies_calculated_values_and_params_without_mutating_result(self):
        before = deepcopy(self.result)
        report = self.report()
        facts = {f["id"]: f["value"] for f in report["facts"]}
        self.assertEqual(facts["report.ml.ml_count"], self.result["ml_count"])
        self.assertEqual(facts["report.ml.contamination"], self.result["contamination"])
        self.assertEqual(facts["report.ml.count.both"], self.result["counts"]["both"])
        self.assertEqual(report["ranges"]["检测开始"], self.result["target_start"])
        self.assertEqual(self.result, before)
        self.assertEqual(len(report["charts"]), 2)
        self.assertEqual(report["charts"][0]["horizontal_line"], self.result["score_threshold"])

    def test_history_allowlist_excludes_arbitrary_metadata(self):
        result = dict(self.result, api_key="PRIVATE_SENTINEL", notes="PRIVATE_NOTE")
        report = build_ml_report(result, FINGERPRINT,
                                 dict(source="history", vehicle=1, reference=2, target=3,
                                      api_key="PRIVATE_SENTINEL", file_name="PRIVATE_FILE"))
        self.assertEqual(report["identifiers"], dict(vehicle=1, reference=2, target=3))
        self.assertNotIn("PRIVATE_", str(report))

    def test_fingerprint_and_result_changes_are_traceable(self):
        first = self.report()
        self.assertEqual(first, self.report())
        changed = build_ml_report(self.result, "b"*64, dict(source="demo"), generated_at=NOW)
        self.assertNotEqual(changed["report_fingerprint"], first["report_fingerprint"])
        for value in ("", "invalid", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_ml_report(self.result, value, dict(source="demo"))

    def test_invalid_and_missing_sources_rejected(self):
        for selection in (dict(source="unknown"), dict(source="history"),
                          dict(source="history", vehicle=1, reference=2, target="PRIVATE")):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                build_ml_report(self.result, FINGERPRINT, selection)

    def test_pdf_is_actual_document_and_errors_do_not_leak(self):
        content = generate_experiment_pdf(self.report())
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertTrue(content.rstrip().endswith(b"%%EOF"))
        with patch("rail_vehicle.experiment_report.SimpleDocTemplate.build", side_effect=ValueError("PRIVATE_RESPONSE")):
            with self.assertRaisesRegex(RuntimeError, "实验报告生成失败") as caught:
                generate_experiment_pdf(self.report())
        self.assertNotIn("PRIVATE_RESPONSE", str(caught.exception))

    def test_non_finite_facts_are_rejected(self):
        result = dict(self.result, score_threshold=float("nan"))
        with self.assertRaises(ValueError):
            build_ml_report(result, FINGERPRINT, dict(source="demo"))


@unittest.skipUnless(HAS_SIGNAL, "Install optional .[signal] dependencies")
class VibrationReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = analyze_vibration(vibration_demo_samples())

    def test_frequency_and_time_metrics_are_copied_from_current_result(self):
        report = build_vibration_report(self.result, FINGERPRINT, dict(source="demo"), generated_at=NOW)
        facts = {f["id"]: f["value"] for f in report["facts"]}
        self.assertEqual(facts["report.vibration.sample_count"], 3000)
        self.assertEqual(facts["report.vibration.lateral_accel.dominant_frequency_hz"],
                         self.result["channels"]["lateral_accel"]["dominant_frequency_hz"])
        self.assertEqual(report["ranges"]["分析结束"], self.result["timestamps_utc"][-1])
        self.assertEqual(len(report["charts"]), 4)
        self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))

    def test_low_rate_preserves_time_metrics_without_fabricating_spectra(self):
        result = analyze_vibration(vibration_demo_samples()[::100])
        report = build_vibration_report(result, FINGERPRINT, dict(source="history", vehicle=1, batch=2))
        self.assertFalse(report["spectral_available"])
        self.assertEqual(report["charts"], [])
        self.assertFalse(any("band_rms" in f["id"] for f in report["facts"]))
        self.assertIn("频域条件不足", " ".join(report["notes"]))
        self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))

    def test_pdf_plot_sampling_preserves_extrema_and_endpoints(self):
        report = build_vibration_report(self.result, FINGERPRINT, dict(source="demo"))
        chart = next(c for c in report["charts"] if "滑动" in c["title"])
        raw = self.result["channels"]["lateral_accel"]
        self.assertLessEqual(len(chart["points"]), 1202)
        self.assertEqual(chart["points"][0], (raw["rolling_end_seconds"][0], raw["rolling_rms"][0]))
        self.assertEqual(chart["points"][-1], (raw["rolling_end_seconds"][-1], raw["rolling_rms"][-1]))
        self.assertEqual(max(y for x, y in chart["points"]), max(raw["rolling_rms"]))

    def test_single_sample_and_undefined_crest_generate_valid_pdf(self):
        result = analyze_vibration([dict(timestamp="2026-01-01T00:00:00Z", speed_kmh=0,
                                        lateral_accel=0, vertical_accel=0)])
        report = build_vibration_report(result, FINGERPRINT, dict(source="demo"))
        self.assertTrue(any(f["value"] is None for f in report["facts"]))
        self.assertTrue(generate_experiment_pdf(report).startswith(b"%PDF-"))


class ExperimentExportGateTests(unittest.TestCase):
    def test_fingerprint_mismatch_cannot_create_export_controls(self):
        def stale_export():
            from rail_vehicle.pages.experiment_export import render_export
            render_export(dict(fingerprint="old", pdf=b"OLD_PRIVATE_PDF"), "new", dict(source="demo"), "ml")
        app = AppTest.from_function(stale_export).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.button), 0)
        self.assertEqual(len(app.get("download_button")), 0)


@unittest.skipUnless(HAS_ML, "Install optional .[ml] dependencies")
class MLReportPageTests(unittest.TestCase):
    def test_generation_uses_saved_result_and_changed_parameters_remove_pdf(self):
        with patch.object(ml_detection, "list_vehicles", return_value=[]), \
             patch.object(ml_detection, "detect_ml_anomalies", wraps=detect_ml_anomalies) as detect:
            app = AppTest.from_function(ml_app).run()
            app.radio(key="ml_source").set_value("内置虚构实验").run()
            self.assertEqual(len(app.get("download_button")), 0)
            app.button(key="ml_train").click().run()
            app.button(key="ml_report_generate").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.get("download_button")), 1)
            self.assertTrue(app.session_state[ml_detection.STATE_KEY]["pdf"].startswith(b"%PDF-"))
            detect.assert_called_once()
            app.number_input(key="ml_rule_threshold").set_value(2.0).run()
            self.assertEqual(len(app.get("download_button")), 0)

    def test_failed_export_keeps_analysis_and_displays_safe_message(self):
        with patch.object(ml_detection, "list_vehicles", return_value=[]):
            app = AppTest.from_function(ml_app).run()
            app.radio(key="ml_source").set_value("内置虚构实验").run()
            app.button(key="ml_train").click().run()
            with patch.object(experiment_export, "generate_experiment_pdf", side_effect=RuntimeError("PRIVATE_ERROR")):
                app.button(key="ml_report_generate").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 4)
        self.assertEqual(len(app.get("download_button")), 0)
        self.assertNotIn("PRIVATE_ERROR", " ".join(item.value for item in app.error))


@unittest.skipUnless(HAS_SIGNAL, "Install optional .[signal] dependencies")
class VibrationReportPageTests(unittest.TestCase):
    def test_export_binds_current_range_without_recomputing_spectra(self):
        with patch.object(vibration_analysis, "list_vehicles", return_value=[]), \
             patch.object(vibration_analysis, "analyze_vibration", wraps=analyze_vibration) as analyze:
            app = AppTest.from_function(vibration_app).run()
            app.radio(key="vibration_source").set_value("内置高频虚构信号").run()
            app.button(key="vibration_calculate").click().run()
            app.button(key="vibration_report_generate").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.get("download_button")), 1)
            analyze.assert_called_once()
            app.slider[0].set_value((0.0, 9.0)).run()
            self.assertEqual(len(app.get("download_button")), 0)
