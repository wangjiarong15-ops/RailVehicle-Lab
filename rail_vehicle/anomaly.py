"""Explainable threshold-based detection of running-data anomalies."""

from datetime import datetime, timezone
import math
from typing import Any

from rail_vehicle.event_anchor import EventAnchor, group_fingerprint
from rail_vehicle.timestamp_utils import parse_iso_timestamp


ACCELERATION_FIELDS = (
    ("lateral_accel", "横向加速度统计异常"),
    ("vertical_accel", "垂向加速度统计异常"),
)
EVENT_DURATION_NOTE = (
    "区段时长按首末异常样本的时间差计算；两者之间存在采样空档时，"
    "不代表空档内持续观测到异常。"
)
EVENT_MERGE_NOTE = ("每个方向独立判定 |a| 严格大于阈值。按 UTC 时刻排序后，"
                    "同方向相邻超阈值采样合为一区段，符号变化和采样空档不分段，"
                    "该方向出现未超阈值记录才分段。")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("样本时间戳必须是 ISO 8601 日期时间字符串。")
    try:
        parsed = parse_iso_timestamp(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("样本时间戳超出可支持的 UTC 时间范围或格式无效。") from exc


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
    return _detect(samples, acceleration_threshold, with_anchors=False)[0]


def detect_anomalies_with_anchors(samples, acceleration_threshold, *, include_rows=False):
    """Return the same event results plus separate local replay anchors in event order."""
    return _detect(samples, acceleration_threshold, with_anchors=True, include_rows=include_rows)


def _detect(samples, acceleration_threshold, *, with_anchors, include_rows=False):
    try:
        threshold = float(acceleration_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("异常阈值必须是有限的非负数。") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("异常阈值必须是有限的非负数。")
    if not samples:
        return {"threshold": threshold, "points": [], "events": []}, []

    try:
        ordered = sorted(samples, key=lambda row: _parse_timestamp(row["timestamp"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("运行数据中存在无效的时间戳。") from exc

    point_records: list[dict[str, Any]] = []
    indexed_events = []
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
                    **({"sample_index": index} if include_rows else {}),
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
            instant = _parse_timestamp(start_time)
            duration = (_parse_timestamp(end_time) - instant).total_seconds()
            event = {
                "type": anomaly_type,
                "field": field,
                "start_timestamp": start_time,
                "end_timestamp": end_time,
                "duration_seconds": duration,
                "sample_count": len(run),
                "maximum_absolute_value": abs(values[peak_index]),
                "maximum_signed_value": values[peak_index],
                "maximum_timestamp": ordered[peak_index]["timestamp"],
                **({"first_index": run[0], "last_index": run[-1], "peak_index": peak_index}
                   if include_rows else {}),
            }
            indexed_events.append((instant, run[0], event))

    indexed_events.sort(key=lambda item: (item[0], item[2]["field"]))
    point_records.sort(
        key=lambda point: (_parse_timestamp(point["timestamp"]), point["field"])
    )
    result = {"threshold": threshold, "points": point_records, "events": [event for _, _, event in indexed_events]}
    if not with_anchors:
        return result, []

    groups = {}
    anchors = []
    for instant, index, _ in indexed_events:
        if instant not in groups:
            first, last = index, index + 1
            while first and _parse_timestamp(ordered[first - 1]["timestamp"]) == instant:
                first -= 1
            while last < len(ordered) and _parse_timestamp(ordered[last]["timestamp"]) == instant:
                last += 1
            groups[instant] = (first, last - first, group_fingerprint(ordered[i] for i in range(first, last)))
        first, size, fingerprint = groups[instant]
        anchors.append(EventAnchor(index - first, size, fingerprint))
    return result, anchors
