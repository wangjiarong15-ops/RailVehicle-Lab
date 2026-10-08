"""Inclusive time-range filtering for running samples."""

from datetime import datetime, timezone
from typing import Any


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("时间范围必须使用有效的 ISO 8601 日期时间。") from exc
    if not isinstance(value, datetime):
        raise ValueError("时间范围必须使用有效的日期时间。")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def filter_samples_by_time_range(
    samples: list[dict[str, Any]], start: datetime | str, end: datetime | str
) -> list[dict[str, Any]]:
    """Keep samples whose timestamps fall within the inclusive UTC range."""
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if start_utc > end_utc:
        raise ValueError("开始时间不能晚于结束时间。")

    filtered: list[dict[str, Any]] = []
    for index, sample in enumerate(samples, start=1):
        try:
            timestamp = _as_utc(sample["timestamp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 个样本的时间戳无效。") from exc
        if start_utc <= timestamp <= end_utc:
            filtered.append(sample)
    return filtered
