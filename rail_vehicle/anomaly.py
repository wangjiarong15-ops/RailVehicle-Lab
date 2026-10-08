"""Explainable threshold-based detection of running-data anomalies."""

from datetime import datetime, timezone
import math
from typing import Any


ACCELERATION_FIELDS = (
    ("lateral_accel", "横向加速度统计异常"),
    ("vertical_accel", "垂向加速度统计异常"),
)


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("样本时间戳必须是 ISO 8601 日期时间字符串。")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def detect_anomalies(
    samples: list[dict[str, Any]], acceleration_threshold: float
) -> dict[str, Any]:
    """Find threshold-exceeding points and merge adjacent same-axis points.

    A point is abnormal when ``abs(acceleration) > acceleration_threshold``.
    Consecutive means adjacent rows in chronological sample order for the same
    acceleration channel. A normal sample splits a segment. Event duration is
    the elapsed time from its first to last abnormal sample; a one-point event
    therefore has a duration of zero seconds.
    """
    try:
        threshold = float(acceleration_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("异常阈值必须是有限的非负数。") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("异常阈值必须是有限的非负数。")
    if not samples:
        return {"threshold": threshold, "points": [], "events": []}

    try:
        ordered = sorted(samples, key=lambda row: _parse_timestamp(row["timestamp"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("运行数据中存在无效的时间戳。") from exc

    point_records: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for field, anomaly_type in ACCELERATION_FIELDS:
        values: list[float] = []
        for index, sample in enumerate(ordered, start=1):
            try:
                value = float(sample[field])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"第 {index} 个样本的 {field} 不是有效数字。"
                ) from exc
            if not math.isfinite(value):
                raise ValueError(f"第 {index} 个样本的 {field} 不是有限数值。")
            values.append(value)

        indices = [
            index for index, value in enumerate(values) if abs(value) > threshold
        ]
        for index in indices:
            point_records.append(
                {
                    "timestamp": ordered[index]["timestamp"],
                    "field": field,
                    "type": anomaly_type,
                    "value": values[index],
                    "absolute_value": abs(values[index]),
                }
            )

        runs: list[list[int]] = []
        for index in indices:
            if not runs or index != runs[-1][-1] + 1:
                runs.append([index])
            else:
                runs[-1].append(index)

        for run in runs:
            peak_index = max(run, key=lambda index: abs(values[index]))
            start_time = ordered[run[0]]["timestamp"]
            end_time = ordered[run[-1]]["timestamp"]
            duration = (
                _parse_timestamp(end_time) - _parse_timestamp(start_time)
            ).total_seconds()
            events.append(
                {
                    "type": anomaly_type,
                    "field": field,
                    "start_timestamp": start_time,
                    "end_timestamp": end_time,
                    "duration_seconds": duration,
                    "sample_count": len(run),
                    "maximum_absolute_value": abs(values[peak_index]),
                    "maximum_signed_value": values[peak_index],
                    "maximum_timestamp": ordered[peak_index]["timestamp"],
                }
            )

    events.sort(
        key=lambda event: (
            _parse_timestamp(event["start_timestamp"]),
            event["field"],
        )
    )
    point_records.sort(
        key=lambda point: (_parse_timestamp(point["timestamp"]), point["field"])
    )
    return {"threshold": threshold, "points": point_records, "events": events}
