"""Local position of a threshold event among samples with the same UTC time."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
from struct import Struct

from rail_vehicle.timestamp_utils import parse_iso_timestamp


_VALUES = Struct("!ddd")
_FIELDS = ("speed_kmh", "lateral_accel", "vertical_accel")


@dataclass(frozen=True)
class EventAnchor:
    occurrence: int  # Zero-based within the original timestamp group and row scope.
    group_size: int
    fingerprint: bytes


def group_fingerprint(rows):
    digest = sha256()
    for row in rows:
        digest.update(_VALUES.pack(*(float(row[field]) for field in _FIELDS)))
    return digest.digest()


def anchors_by_evidence(payload, anchors):
    """Keep the local anchors separate from the validated provider payload."""
    return {event["evidence_id"]: anchor for event, anchor in zip(payload["anomaly_events"], anchors, strict=True)}


def resolve_event_row(track, timestamp, axis, threshold, *, row_span=None, anchor=None):
    """Return a verified row, or None when legacy evidence is ambiguous or stale."""
    field = {"lateral": "lateral_accel", "vertical": "vertical_accel"}.get(axis)
    if field is None:
        return None
    instant = parse_iso_timestamp(timestamp)
    instant = instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)
    offset = (instant - parse_iso_timestamp(track.samples[0]["timestamp"])).total_seconds()
    first, last = bisect_left(track.offsets, offset), bisect_right(track.offsets, offset)
    if row_span is not None:
        start_row, end_row = row_span
        if not 1 <= start_row <= end_row <= len(track.samples):
            return None
        first, last = max(first, start_row - 1), min(last, end_row)
    # Compare exact UTC strings as nearby microseconds can share a float offset.
    utc_text = instant.isoformat(sep=" ")
    group = [i for i in range(first, last) if track.samples[i]["timestamp"] == utc_text]
    if anchor is not None:
        if (len(group) != anchor.group_size or not 0 <= anchor.occurrence < len(group)
                or group_fingerprint(track.samples[i] for i in group) != anchor.fingerprint):
            return None
        index = group[anchor.occurrence]
        return index if abs(track.samples[index][field]) > threshold else None
    candidates = [i for i in group if abs(track.samples[i][field]) > threshold]
    return candidates[0] if len(candidates) == 1 else None
