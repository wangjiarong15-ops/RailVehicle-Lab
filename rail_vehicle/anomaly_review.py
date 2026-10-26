"""Read-only windows around already detected rule events."""

from datetime import timezone
import math

from rail_vehicle.timestamp_utils import parse_iso_timestamp


def _utc(value):
    parsed = parse_iso_timestamp(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _visible(rows, limit=2000):
    if len(rows) <= limit:
        return rows
    return [rows[round(i * (len(rows) - 1) / (limit - 1))] for i in range(limit)]


def event_window(samples, anomalies, event_index, seconds):
    """Keep current filtered samples only; do not detect or merge events again."""
    events = anomalies["events"]
    if isinstance(event_index, bool) or not isinstance(event_index, int) or not 0 <= event_index < len(events):
        raise ValueError("请选择有效异常事件。")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 60:
        raise ValueError("事件前后窗口必须在 0–60 秒之间。")
    event = events[event_index]
    start, end = _utc(event["start_timestamp"]), _utc(event["end_timestamp"])
    duration = (end - start).total_seconds()
    rows = []
    for index, sample in enumerate(samples):
        offset = (_utc(sample["timestamp"]) - start).total_seconds()
        if -seconds <= offset <= duration + seconds:
            rows.append(dict(sample, relative_seconds=offset, source_index=index))
    rows.sort(key=lambda row: (row["relative_seconds"], row["source_index"]))
    points = []
    for point in anomalies["points"]:
        offset = (_utc(point["timestamp"]) - start).total_seconds()
        if point["field"] == event["field"] and -seconds <= offset <= duration + seconds:
            points.append(dict(point, relative_seconds=offset))
    return dict(event=dict(event), rows=rows, visible=_visible(rows),
                points=points, visible_points=_visible(points), duration=duration)
