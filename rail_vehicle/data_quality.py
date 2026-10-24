"""Descriptive sampling-time summaries for imported running data."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from statistics import median
from typing import Any


def _utc_timestamp(value: Any) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("运行数据中存在无效的时间戳。") from exc


def summarize_timestamps(timestamps: Iterable[Any]) -> dict[str, Any]:
    """Summarize duplicate timestamps and spacing between unique instants."""
    unique_timestamps: set[datetime] = set()
    sample_count = 0
    try:
        for value in timestamps:
            unique_timestamps.add(_utc_timestamp(value))
            sample_count += 1
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("运行数据中存在无效的时间戳。") from exc
    if sample_count == 0:
        raise ValueError("所选范围没有运行数据。")

    ordered_timestamps = sorted(unique_timestamps)
    intervals = [
        (current - previous).total_seconds()
        for previous, current in zip(ordered_timestamps, ordered_timestamps[1:])
    ]
    return {
        "sample_count": sample_count,
        "unique_timestamp_count": len(ordered_timestamps),
        "duplicate_timestamp_rows": sample_count - len(ordered_timestamps),
        "interval_min_seconds": min(intervals) if intervals else None,
        "interval_median_seconds": median(intervals) if intervals else None,
        "interval_max_seconds": max(intervals) if intervals else None,
    }


def analyze_sampling_times(
    samples: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize sampling distribution for rows with timestamp fields.

    Duplicate rows count additional records that share an absolute timestamp.
    Intervals are calculated after sorting and deduplicating timestamps in UTC.
    No judgment about data quality or acceptable gaps is made.
    """
    timestamps = (sample["timestamp"] for sample in samples)
    return summarize_timestamps(timestamps)
