"""Basic, unit-aware statistics for imported vehicle running samples."""

from datetime import datetime, timezone
import math
from typing import Any


def _time_order(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validated_values(samples: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for index, sample in enumerate(samples, start=1):
        try:
            value = float(sample[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 个样本的 {field} 不是有效数字。") from exc
        if not math.isfinite(value):
            raise ValueError(f"第 {index} 个样本的 {field} 不是有限数值。")
        values.append(value)
    return values


def _acceleration_statistics(
    ordered_samples: list[dict[str, Any]], values: list[float]
) -> dict[str, Any]:
    peak_index = max(range(len(values)), key=lambda index: abs(values[index]))
    return {
        "maximum": max(values),
        "minimum": min(values),
        "mean": sum(values) / len(values),
        "rms": math.sqrt(sum(value * value for value in values) / len(values)),
        "peak_absolute": abs(values[peak_index]),
        "peak_signed": values[peak_index],
        "peak_timestamp": ordered_samples[peak_index]["timestamp"],
    }


def _exceedance(values: list[float], threshold: float) -> dict[str, float | int]:
    count = sum(abs(value) > threshold for value in values)
    return {"count": count, "percentage": count / len(values) * 100}


def analyze_run_data(
    samples: list[dict[str, Any]], acceleration_threshold: float = 1.0
) -> dict[str, Any]:
    """Calculate descriptive statistics and threshold counts for one run.

    Acceleration peaks are absolute magnitudes; the signed sample and its
    timestamp are also returned. Threshold exceedance uses strict absolute
    magnitude comparison (abs(value) > threshold).
    """
    if not samples:
        raise ValueError("所选车辆和导入批次没有运行数据。")
    try:
        threshold = float(acceleration_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("加速度阈值必须是有限的非负数。") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("加速度阈值必须是有限的非负数。")

    try:
        ordered_samples = sorted(samples, key=lambda row: _time_order(row["timestamp"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("运行数据中存在无效的时间戳。") from exc

    speed = _validated_values(ordered_samples, "speed_kmh")
    lateral = _validated_values(ordered_samples, "lateral_accel")
    vertical = _validated_values(ordered_samples, "vertical_accel")
    speed_statistics = {
        "maximum": max(speed),
        "minimum": min(speed),
        "mean": sum(speed) / len(speed),
    }
    lateral_statistics = _acceleration_statistics(ordered_samples, lateral)
    vertical_statistics = _acceleration_statistics(ordered_samples, vertical)

    lateral_exceedance = _exceedance(lateral, threshold)
    vertical_exceedance = _exceedance(vertical, threshold)
    combined_count = sum(
        abs(row["lateral_accel"]) > threshold
        or abs(row["vertical_accel"]) > threshold
        for row in ordered_samples
    )
    threshold_statistics = {
        "value": threshold,
        "lateral": lateral_exceedance,
        "vertical": vertical_exceedance,
        "either": {
            "count": combined_count,
            "percentage": combined_count / len(ordered_samples) * 100,
        },
    }
    return {
        "sample_count": len(ordered_samples),
        "samples": ordered_samples,
        "speed": speed_statistics,
        "lateral_accel": lateral_statistics,
        "vertical_accel": vertical_statistics,
        "threshold": threshold_statistics,
    }
