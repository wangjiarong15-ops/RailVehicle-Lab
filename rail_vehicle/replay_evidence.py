"""Offline evidence handoff for observed rows in a replay window."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO

from rail_vehicle.replay import comparison_reference
from rail_vehicle.timestamp_utils import parse_iso_timestamp


MAX_WINDOW_EXPORT_ROWS = 20_000
_OBSERVATION_FIELDS = ("source", "batch_id", "local_vehicle_id", "batch_row", "timestamp_utc", "batch_elapsed_s",
                       "speed_kmh", "lateral_accel_m_s2", "vertical_accel_m_s2", "threshold_m_s2",
                       "lateral_exceeds", "vertical_exceeds")


def valid_row_span(span, count):
    """Check a one-based, inclusive row span against the observation count."""
    return (isinstance(span, (tuple, list)) and len(span) == 2
            and all(type(row) is int and row > 0 for row in span) and span[1] - span[0] + 1 == count > 0)


def _utc(value):
    instant = parse_iso_timestamp(value)
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)


@dataclass(frozen=True)
class ComparisonObservation:
    row: int
    timestamp: str
    speed_kmh: float
    lateral_accel: float
    vertical_accel: float

    @classmethod
    def capture(cls, row, sample):
        return cls(row, _utc(sample["timestamp"]).isoformat(sep=" "),
                   *(float(sample[field]) for field in ("speed_kmh", "lateral_accel", "vertical_accel")))


@dataclass(frozen=True)
class ReportComparison:
    """A session-only snapshot, checked against absolute batch rows before use."""

    vehicle_id: int | None
    batch_id: int
    fictional: bool
    batch_count: int
    threshold: float
    speed_tolerance: float | None
    current: ComparisonObservation
    reference: ComparisonObservation

    @classmethod
    def capture(cls, track, current, reference, *, vehicle_id, batch_id, fictional, threshold, speed_tolerance):
        return cls(vehicle_id, batch_id, fictional, len(track.samples), threshold, speed_tolerance,
                   *(ComparisonObservation.capture(i + 1, track.samples[i]) for i in (current, reference)))

    def matches_batch(self, vehicle_id, batch_id, fictional):
        return (self.vehicle_id, self.batch_id, self.fictional) == (None if fictional else vehicle_id, batch_id, fictional)

    def matches_scope(self, samples, *, threshold, start, end, row_span, first_row=1, batch_count=None):
        """Validate by absolute row using either the full batch or a contiguous UTC slice."""
        batch_count = len(samples) if batch_count is None else batch_count
        if batch_count != self.batch_count or self.threshold != threshold:
            return False
        first, last = row_span or (1, batch_count)
        for observation in (self.current, self.reference):
            row = observation.row
            position = row - first_row
            if not (1 <= row <= batch_count and first <= row <= last and 0 <= position < len(samples)
                    and start <= _utc(observation.timestamp) <= end):
                return False
            if observation != ComparisonObservation.capture(row, samples[position]):
                return False
        return True


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

    @classmethod
    def from_analysis_slice(cls, samples, *, first_row, batch_count):
        """Map a chronological slice to the replay track's zero-based rows."""
        if not samples or len(samples) == batch_count:
            return None
        last_row = first_row + len(samples) - 1
        if not 0 <= first_row <= last_row < batch_count:
            raise ValueError("Analysis slice does not fit its batch")
        return cls(first_row, last_row, batch_count, _utc(samples[0]["timestamp"]), _utc(samples[-1]["timestamp"]))

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


def _observation(track, index, *, batch_id, vehicle_id, threshold, fictional):
    sample = track.samples[index]
    lateral, vertical = sample["lateral_accel"], sample["vertical_accel"]
    return ("fictional_demo" if fictional else "historical_import", batch_id, vehicle_id, index + 1,
            sample["timestamp"], track.offsets[index], sample["speed_kmh"], lateral, vertical, threshold,
            int(abs(lateral) > threshold), int(abs(vertical) > threshold))


def replay_evidence_csv(track, scope, *, batch_id, vehicle_id, threshold, fictional):
    """Export values, row identity and threshold flags; never insert estimated samples."""
    if not 0 < scope.count <= MAX_WINDOW_EXPORT_ROWS:
        raise ValueError("当前窗口超出 CSV 导出行数限制。")
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(_OBSERVATION_FIELDS)
    for index in range(scope.first_row, scope.last_row + 1):
        writer.writerow(_observation(track, index, batch_id=batch_id, vehicle_id=vehicle_id,
                                     threshold=threshold, fictional=fictional))
    return buffer.getvalue().encode("utf-8-sig")


def comparison_evidence_csv(track, current, reference, *, batch_id, vehicle_id, fictional,
                            threshold, cutoff, speed_tolerance, first=0, last=None):
    """Export just the exact comparison pair and its selection settings, not the model pose."""
    last = len(track.samples) - 1 if last is None else last
    if reference is None or comparison_reference(track, current, threshold, cutoff, first=first, last=last,
                                                 speed_tolerance=speed_tolerance) != reference:
        raise ValueError("对照样本与当前筛选条件不匹配。")
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(("comparison_role", *_OBSERVATION_FIELDS, "window_first_row", "window_last_row",
                     "gap_cutoff_s", "speed_tolerance_kmh", "pair_time_difference_s", "pair_speed_difference_kmh"))
    parameters = (first + 1, last + 1, cutoff, speed_tolerance,
                  abs(track.offsets[current] - track.offsets[reference]),
                  abs(track.samples[current]["speed_kmh"] - track.samples[reference]["speed_kmh"]))
    for role, index in (("current_exceedance", current), ("reference_not_exceeding", reference)):
        writer.writerow((role, *_observation(track, index, batch_id=batch_id, vehicle_id=vehicle_id,
                                             threshold=threshold, fictional=fictional), *parameters))
    return buffer.getvalue().encode("utf-8-sig")
