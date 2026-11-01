"""Descriptive sampling-time summaries for imported running data."""

from collections.abc import Iterable, Mapping
import math
from datetime import datetime, timezone
from statistics import median
from typing import Any

from rail_vehicle.timestamp_utils import parse_iso_timestamp


def _utc_timestamp(value: Any) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else parse_iso_timestamp(value)
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


def review_sampling_timestamps(
    timestamps: Iterable[Any], *, expected_interval_seconds: float | None = None,
    gap_multiplier: float = 3.0, detail_limit: int = 100,
) -> dict[str, Any]:
    """Flag timestamp concerns without dropping, filling or reordering source data.

    Gaps strictly exceed multiplier × baseline. The inferred median is descriptive,
    not an acquisition specification; a known interval should be supplied when available.
    Reversals compare adjacent input rows, not the sorted unique timeline.
    """
    if (isinstance(gap_multiplier, bool) or not isinstance(gap_multiplier, (int, float))
            or not math.isfinite(gap_multiplier) or gap_multiplier <= 1):
        raise ValueError("长间隔倍数必须为大于 1 的有限数值。")
    if expected_interval_seconds is not None and (
        isinstance(expected_interval_seconds, bool)
        or not isinstance(expected_interval_seconds, (int, float))
        or not math.isfinite(expected_interval_seconds) or expected_interval_seconds <= 0
    ):
        raise ValueError("预期采样间隔必须为大于 0 的有限数值。")
    if isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or detail_limit < 0:
        raise ValueError("明细上限必须为非负整数。")
    try:
        instants = [_utc_timestamp(value) for value in timestamps]
    except (TypeError, ValueError) as exc:
        raise ValueError("运行数据中存在无效的时间戳。") from exc
    summary = summarize_timestamps(instants)
    baseline = (expected_interval_seconds if expected_interval_seconds is not None
                else summary["interval_median_seconds"])
    cutoff = baseline * gap_multiplier if baseline is not None else None
    if cutoff is not None and not math.isfinite(cutoff):
        raise ValueError("采样间隔与倍数的乘积超出可支持范围。")
    ordered = sorted(set(instants))
    gaps = []
    gap_count = 0
    for previous, current in zip(ordered, ordered[1:]):
        interval = (current - previous).total_seconds()
        if cutoff is not None and interval > cutoff:
            gap_count += 1
            if len(gaps) < detail_limit:
                gaps.append({"start_timestamp": previous.isoformat(),
                             "end_timestamp": current.isoformat(),
                             "interval_seconds": interval})
    reversals = sum(current < previous for previous, current in zip(instants, instants[1:]))
    concerns = bool(summary["duplicate_timestamp_rows"] or reversals or gap_count)
    return {**summary, "baseline_seconds": baseline,
            "baseline_source": "specified" if expected_interval_seconds is not None else "median",
            "gap_multiplier": gap_multiplier, "gap_cutoff_seconds": cutoff,
            "out_of_order_pairs": reversals, "long_gap_count": gap_count,
            "long_gaps": gaps, "details_truncated": gap_count > len(gaps),
            "status": "needs_review" if concerns else (
                "insufficient" if len(ordered) < 2 else "no_flags")}
