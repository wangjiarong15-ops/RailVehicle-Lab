"""Local review of existing ML labels; never fit, score, or classify samples."""

from datetime import timezone
import math

from rail_vehicle.timestamp_utils import parse_iso_timestamp


def flagged_indices(records, category="flagged"):
    if category not in {"flagged", "both", "ml_only", "rule_only"}:
        raise ValueError("样本回查分类无效。")
    return [index for index, row in enumerate(records)
            if row["category"] != "neither" and (category == "flagged" or row["category"] == category)]


def _utc(value):
    parsed = parse_iso_timestamp(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def sample_window(records, selected_index, seconds, *, plot_limit=2000):
    """Inclusive UTC window, keeping duplicate records and the selected point."""
    if isinstance(selected_index, bool) or not isinstance(selected_index, int) or not 0 <= selected_index < len(records):
        raise ValueError("请选择有效样本。")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 60:
        raise ValueError("回查窗口必须在 0–60 秒之间。")
    if isinstance(plot_limit, bool) or not isinstance(plot_limit, int) or not 3 <= plot_limit <= 2000:
        raise ValueError("回查图表点数限制无效。")
    center = _utc(records[selected_index]["timestamp"])
    rows = []
    for index, record in enumerate(records):
        offset = (_utc(record["timestamp"]) - center).total_seconds()
        if abs(offset) <= seconds:
            rows.append(dict(record, source_index=index, relative_seconds=offset))
    rows.sort(key=lambda row: (row["relative_seconds"], row["source_index"]))
    selected_position = next(i for i, row in enumerate(rows) if row["source_index"] == selected_index)
    if len(rows) <= plot_limit:
        visible = rows
    else:
        indices = {round(i*(len(rows)-1)/(plot_limit-1)) for i in range(plot_limit)} | {selected_position}
        visible = [rows[i] for i in sorted(indices)]
    return dict(selected=dict(records[selected_index]), selected_index=selected_index,
                rows=rows, visible=visible, window_seconds=seconds)
