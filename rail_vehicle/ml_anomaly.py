"""Local Isolation Forest baseline, independent of AI services and rule detection."""

import hashlib
import json
import math
from typing import Any

from rail_vehicle.replay import prepare_replay


FEATURES = ("speed_kmh", "lateral_accel", "vertical_accel")
MIN_REFERENCE_SAMPLES = 32
MAX_DATASET_SAMPLES = 20_000
MODEL_SEED = 42
TREE_COUNT = 100
STATE_KEY = "ml_detection_result"


class MLUnavailableError(RuntimeError):
    """The optional local learning dependency is not installed."""


class MLValidationError(ValueError):
    """A fixed user-safe input or model-result validation message."""


def estimator_class():
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        raise MLUnavailableError("机器学习依赖未安装，请安装项目的 ml 可选依赖。") from None
    return IsolationForest


def analysis_fingerprint(reference, target, *, contamination, rule_threshold, selection):
    """Bind session-only results to actual inputs, parameters and selected batches."""
    content = json.dumps(
        [reference, target, contamination, rule_threshold, selection],
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _prepare(samples, name):
    if not samples:
        raise MLValidationError(f"{name}没有运行数据。")
    if len(samples) > MAX_DATASET_SAMPLES:
        raise MLValidationError(f"{name}超过本地基线的 {MAX_DATASET_SAMPLES:,} 条上限，请使用较小批次。")
    try:
        rows = prepare_replay(samples).samples
    except (ValueError, TypeError, KeyError, OverflowError):
        raise MLValidationError(f"{name}包含无效时间戳或非有限数值。") from None
    # Same physical limits as CSV imports. No silent clipping or imputation.
    if any(not 0 <= r["speed_kmh"] <= 600 or
           abs(r["lateral_accel"]) > 100 or abs(r["vertical_accel"]) > 100 for r in rows):
        raise MLValidationError(f"{name}包含超出 CSV 导入范围的数值。")
    return rows


def detect_ml_anomalies(
    reference_samples: list[dict[str, Any]], target_samples: list[dict[str, Any]],
    *, contamination: float = 0.05, rule_threshold: float = 1.0,
) -> dict[str, Any]:
    """Learn only from reference rows; score target rows without fitting on them.

    Larger ``-score_samples`` means more unusual. This is not a probability.
    The cutoff comes from the reference distribution via model.offset_.
    Rule labels use the unchanged strict absolute-acceleration criterion.
    """
    if not isinstance(contamination, (float, int)) or not math.isfinite(contamination) or not 0 < contamination <= 0.5:
        raise MLValidationError("参考离群比例必须大于 0 且不超过 0.5。")
    if not isinstance(rule_threshold, (float, int)) or not math.isfinite(rule_threshold) or rule_threshold < 0:
        raise MLValidationError("加速度阈值必须是有限的非负数。")
    reference = _prepare(reference_samples, "参考批次")
    target = _prepare(target_samples, "待检测批次")
    if len(reference) < MIN_REFERENCE_SAMPLES:
        raise MLValidationError(f"参考批次至少需要 {MIN_REFERENCE_SAMPLES} 条记录。")
    reference_features = [[r[field] for field in FEATURES] for r in reference]
    if len({tuple(vector) for vector in reference_features}) < 2:
        raise MLValidationError("参考批次的特征完全相同，无法学习可用的离群基线。")
    model = estimator_class()(
        n_estimators=TREE_COUNT, max_samples=min(256, len(reference)),
        contamination=contamination, random_state=MODEL_SEED, n_jobs=1,
    )
    model.fit(reference_features)
    target_features = [[r[field] for field in FEATURES] for r in target]
    scores = [-float(value) for value in model.score_samples(target_features)]
    score_threshold = -float(model.offset_)
    if not math.isfinite(score_threshold) or any(not math.isfinite(score) for score in scores):
        raise MLValidationError("模型未生成有效的有限分数，本次结果不展示。")
    counts = dict(both=0, ml_only=0, rule_only=0, neither=0)
    records = []
    for sample, score in zip(target, scores):
        ml_flag = score > score_threshold
        rule_flag = abs(sample["lateral_accel"]) > rule_threshold or abs(sample["vertical_accel"]) > rule_threshold
        category = "both" if ml_flag and rule_flag else "ml_only" if ml_flag else "rule_only" if rule_flag else "neither"
        counts[category] += 1
        records.append({**sample, "ml_score": score, "ml_anomaly": ml_flag,
                        "rule_anomaly": rule_flag, "category": category})
    return {
        "records": records, "counts": counts, "sample_count": len(target),
        "ml_count": counts["both"] + counts["ml_only"],
        "rule_count": counts["both"] + counts["rule_only"],
        "score_threshold": score_threshold,
        "reference_count": len(reference), "contamination": contamination,
        "rule_threshold": rule_threshold, "seed": MODEL_SEED,
        "tree_count": TREE_COUNT, "features": list(FEATURES),
        "reference_start": reference[0]["timestamp"], "reference_end": reference[-1]["timestamp"],
        "target_start": target[0]["timestamp"], "target_end": target[-1]["timestamp"],
    }
