"""Core calculations for comparing running-data import batches."""

from datetime import datetime, timezone
from typing import Any

from rail_vehicle.dynamics import analyze_run_data


def _as_utc(timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compare_run_batches(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize two or more batches and return time-aligned chart series.

    Each input item must contain ``batch_id``, ``label`` and ``samples``. All
    samples in a batch are ordered chronologically and relative time starts at
    zero for that batch's first sample.
    """
    if len(batches) < 2:
        raise ValueError("批次对比至少需要选择两个运行批次。")

    comparisons: list[dict[str, Any]] = []
    for batch in batches:
        try:
            analysis = analyze_run_data(batch["samples"])
            ordered_samples = analysis["samples"]
            first_timestamp = _as_utc(ordered_samples[0]["timestamp"])
        except KeyError as exc:
            raise ValueError("批次数据缺少批次编号、名称或运行样本。") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"批次 {batch.get('batch_id', '')} 数据无效：{exc}") from exc

        lateral = analysis["lateral_accel"]
        vertical = analysis["vertical_accel"]
        series = []
        for sample in ordered_samples:
            try:
                relative_seconds = (
                    _as_utc(sample["timestamp"]) - first_timestamp
                ).total_seconds()
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"批次 {batch.get('batch_id', '')} 包含无效时间戳。"
                ) from exc
            series.append(
                {
                    "relative_time_seconds": relative_seconds,
                    "speed_kmh": float(sample["speed_kmh"]),
                    "lateral_accel": float(sample["lateral_accel"]),
                    "vertical_accel": float(sample["vertical_accel"]),
                }
            )

        comparisons.append(
            {
                "batch_id": batch["batch_id"],
                "label": str(batch["label"]),
                "sample_count": analysis["sample_count"],
                "mean_speed_kmh": analysis["speed"]["mean"],
                "maximum_speed_kmh": analysis["speed"]["maximum"],
                "lateral_accel_rms": lateral["rms"],
                "vertical_accel_rms": vertical["rms"],
                "lateral_accel_peak_absolute": lateral["peak_absolute"],
                "vertical_accel_peak_absolute": vertical["peak_absolute"],
                "series": series,
            }
        )
    return comparisons
