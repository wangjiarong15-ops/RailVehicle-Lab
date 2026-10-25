"""Isolation Forest behavior and local-only experiment boundaries."""

import builtins
from copy import deepcopy
import importlib.util
import math
import unittest
from unittest.mock import Mock, patch

from rail_vehicle.demo_data import ml_demo_samples
from rail_vehicle.ml_anomaly import (
    FEATURES, MODEL_SEED, TREE_COUNT, MLUnavailableError,
    analysis_fingerprint, detect_ml_anomalies, estimator_class,
)


@unittest.skipUnless(importlib.util.find_spec("sklearn"), "Install optional .[ml] dependencies")
class IsolationForestTests(unittest.TestCase):
    def test_independent_demo_detects_novel_speed_and_acceleration_excursions(self):
        reference, target = ml_demo_samples()
        result = detect_ml_anomalies(reference, target)
        self.assertEqual(result["sample_count"], 160)
        self.assertEqual(result["reference_count"], 400)
        self.assertEqual(result["rule_count"], 5)
        self.assertTrue(all(r["ml_anomaly"] and not r["rule_anomaly"] for r in result["records"][40:46]))
        self.assertTrue(all(r["category"] == "both" for r in result["records"][100:105]))
        self.assertEqual(sum(result["counts"].values()), 160)
        self.assertTrue(all(math.isfinite(r["ml_score"]) for r in result["records"]))
        self.assertEqual(set(reference[0]), {"timestamp", *FEATURES})  # No labels in the features.

    def test_repeated_experiments_are_reproducible_and_inputs_unchanged(self):
        reference, target = ml_demo_samples()
        original = deepcopy((reference, target))
        first = detect_ml_anomalies(reference, target)
        second = detect_ml_anomalies(reference, target)
        self.assertEqual(first, second)
        self.assertEqual((reference, target), original)

    def test_target_changes_cannot_change_reference_learned_cutoff(self):
        reference, target = ml_demo_samples()
        first = detect_ml_anomalies(reference, target)
        altered = deepcopy(target)
        for row in altered:
            row["speed_kmh"] = 300
        second = detect_ml_anomalies(reference, altered)
        self.assertEqual(first["score_threshold"], second["score_threshold"])


class MLContractTests(unittest.TestCase):
    def setUp(self):
        self.reference, self.target = ml_demo_samples()

    def test_only_reference_features_are_fitted_and_threshold_is_strict(self):
        model = Mock(offset_=-0.6)
        model.score_samples.return_value = [-0.6, -0.8]
        constructor = Mock(return_value=model)
        target = deepcopy(self.target[:2])
        target[0]["lateral_accel"] = 1.0
        target[1]["vertical_accel"] = -2.0
        with patch("rail_vehicle.ml_anomaly.estimator_class", return_value=constructor):
            result = detect_ml_anomalies(self.reference, target)
        constructor.assert_called_once_with(n_estimators=TREE_COUNT, max_samples=256,
                                            contamination=0.05, random_state=MODEL_SEED, n_jobs=1)
        fitted = model.fit.call_args.args[0]
        self.assertEqual(fitted, [[r[field] for field in FEATURES] for r in self.reference])
        scored = model.score_samples.call_args.args[0]
        self.assertEqual(scored, [[r[field] for field in FEATURES] for r in target])
        self.assertEqual(result["records"][0]["category"], "neither")
        self.assertEqual(result["records"][1]["category"], "both")

    def test_empty_small_constant_and_oversized_reference_are_rejected(self):
        for reference in ([], self.reference[:31], [self.reference[0]] * 32):
            with self.subTest(size=len(reference)), self.assertRaises(ValueError):
                detect_ml_anomalies(reference, self.target)
        with patch("rail_vehicle.ml_anomaly.MAX_DATASET_SAMPLES", 100):
            with self.assertRaisesRegex(ValueError, "上限"):
                detect_ml_anomalies(self.reference, self.target)
        with self.assertRaisesRegex(ValueError, "待检测批次"):
            detect_ml_anomalies(self.reference, [])

    def test_invalid_numeric_time_and_parameters_are_rejected(self):
        for field, value in (("speed_kmh", float("nan")), ("speed_kmh", 601),
                             ("lateral_accel", float("inf")), ("vertical_accel", -101),
                             ("timestamp", "not-a-time")):
            target = deepcopy(self.target[:1])
            target[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                detect_ml_anomalies(self.reference, target)
        for contamination in (0, -0.1, 0.6, float("nan")):
            with self.subTest(contamination=contamination), self.assertRaises(ValueError):
                detect_ml_anomalies(self.reference, self.target, contamination=contamination)
        with self.assertRaises(ValueError):
            detect_ml_anomalies(self.reference, self.target, rule_threshold=-1)

    def test_nonfinite_model_output_is_rejected(self):
        model = Mock(offset_=-0.6)
        model.score_samples.return_value = [float("nan")]
        with patch("rail_vehicle.ml_anomaly.estimator_class", return_value=Mock(return_value=model)):
            with self.assertRaisesRegex(ValueError, "有限分数"):
                detect_ml_anomalies(self.reference, self.target[:1])

    def test_missing_optional_dependency_has_fixed_message(self):
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name == "sklearn.ensemble":
                raise ImportError("PRIVATE_DEPENDENCY_DETAIL")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded_import):
            with self.assertRaises(MLUnavailableError) as raised:
                estimator_class()
        self.assertNotIn("PRIVATE_DEPENDENCY_DETAIL", str(raised.exception))

    def test_fingerprint_binds_samples_parameters_and_selection(self):
        params = dict(contamination=0.05, rule_threshold=1.0, selection=dict(vehicle=1, reference=2, target=3))
        original = analysis_fingerprint(self.reference, self.target, **params)
        for key, value in (("contamination", 0.1), ("rule_threshold", 2),
                            ("selection", dict(vehicle=2, reference=2, target=3))):
            changed = {**params, key: value}
            self.assertNotEqual(original, analysis_fingerprint(self.reference, self.target, **changed))
        changed_target = deepcopy(self.target)
        changed_target[0]["speed_kmh"] += 1
        self.assertNotEqual(original, analysis_fingerprint(self.reference, changed_target, **params))
