"""Offline evidence handoff for observed rows in a replay window."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO

from rail_vehicle.timestamp_utils import parse_iso_timestamp


MAX_WINDOW_EXPORT_ROWS = 20_000


def valid_row_span(span, count):
    """Check a one-based, inclusive row span against the observation count."""
    return (isinstance(span, (tuple, list)) and len(span) == 2
            and all(type(row) is int and row > 0 for row in span) and span[1] - span[0] + 1 == count > 0)


def _utc(value):
    instant = parse_iso_timestamp(value)
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReplayEvidenceScope:
    first_row: int
    last_row: int
    batch_count: int
    first_timestamp: datetime
    last_timestamp: datetime

    @classmethod
    def from_window(cls, track, window):
        first, last = window.first_row, window.last_row
        return cls(first, last, len(track.samples), _utc(track.samples[first]["timestamp"]),
                   _utc(track.samples[last]["timestamp"]))

    @property
    def count(self):
        return self.last_row - self.first_row + 1

    def matches(self, timestamps):
        return (self._valid_bounds(len(timestamps))
                and timestamps[self.first_row] == self.first_timestamp
                and timestamps[self.last_row] == self.last_timestamp)

    def matches_track(self, track):
        return (self._valid_bounds(len(track.samples))
                and _utc(track.samples[self.first_row]["timestamp"]) == self.first_timestamp
                and _utc(track.samples[self.last_row]["timestamp"]) == self.last_timestamp)

    def _valid_bounds(self, length):
        return length == self.batch_count and 0 <= self.first_row <= self.last_row < length


def replay_evidence_csv(track, scope, *, batch_id, vehicle_id, threshold, fictional):
    """Export values, row identity and threshold flags; never insert estimated samples."""
    if not 0 < scope.count <= MAX_WINDOW_EXPORT_ROWS:
        raise ValueError("当前窗口超出 CSV 导出行数限制。")
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(("source", "batch_id", "local_vehicle_id", "batch_row", "timestamp_utc", "batch_elapsed_s",
                     "speed_kmh", "lateral_accel_m_s2", "vertical_accel_m_s2", "threshold_m_s2",
                     "lateral_exceeds", "vertical_exceeds"))
    source = "fictional_demo" if fictional else "historical_import"
    for index in range(scope.first_row, scope.last_row + 1):
        sample = track.samples[index]
        lateral, vertical = sample["lateral_accel"], sample["vertical_accel"]
        writer.writerow((source, batch_id, vehicle_id, index + 1, sample["timestamp"],
                         track.offsets[index], sample["speed_kmh"], lateral, vertical, threshold,
                         int(abs(lateral) > threshold), int(abs(vertical) > threshold)))
    return buffer.getvalue().encode("utf-8-sig")
