"""Deterministic, clock-independent historical sample replay."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import timezone
import math
from typing import Any

from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.timestamp_utils import parse_iso_timestamp


@dataclass(frozen=True)
class ReplayTrack:
    samples: list[dict[str, Any]]
    offsets: tuple[float, ...]

    @property
    def duration(self) -> float:
        return self.offsets[-1]

    def index_at(self, position: float) -> int:
        return max(0, bisect_right(self.offsets, position) - 1)


@dataclass(frozen=True)
class ReplayState:
    position: float = 0.0
    playing: bool = False
    rate: float = 1.0
    last_tick: float | None = None


def prepare_replay(samples: list[dict[str, Any]]) -> ReplayTrack:
    """Reuse existing numeric validation; preserve actual UTC sample spacing."""
    ordered = analyze_run_data(samples)["samples"]
    normalized = []
    instants = []
    for sample in ordered:
        timestamp = parse_iso_timestamp(sample["timestamp"])
        timestamp = (
            timestamp.replace(tzinfo=timezone.utc)
            if timestamp.tzinfo is None else timestamp.astimezone(timezone.utc)
        )
        instants.append(timestamp)
        normalized.append({
            "timestamp": timestamp.isoformat(sep=" "),
            **{field: float(sample[field]) for field in
               ("speed_kmh", "lateral_accel", "vertical_accel")},
        })
    offsets = tuple((instant - instants[0]).total_seconds() for instant in instants)
    return ReplayTrack(normalized, offsets)


def advance_replay(state: ReplayState, now: float, duration: float) -> ReplayState:
    """Advance by monotonic wall-clock time, stopping exactly at the last sample."""
    if not state.playing or state.last_tick is None:
        return state
    position = min(duration, state.position + max(0.0, now - state.last_tick) * state.rate)
    playing = position < duration
    return replace(state, position=position, playing=playing, last_tick=now if playing else None)


def control_replay(
    state: ReplayState, action: str, now: float, duration: float,
    *, rate: float | None = None,
) -> ReplayState:
    """Apply controls after accounting for time spent at the previous rate."""
    state = advance_replay(state, now, duration)
    if action == "play":
        position = 0.0 if state.position >= duration else state.position
        return replace(state, position=position, playing=duration > 0,
                       last_tick=now if duration > 0 else None)
    if action == "pause":
        return replace(state, playing=False, last_tick=None)
    if action == "reset":
        return ReplayState(rate=state.rate)
    if action == "rate":
        if rate is None or not math.isfinite(rate) or rate <= 0:
            raise ValueError("回放倍速必须是有限的正数。")
        return replace(state, rate=rate)
    raise ValueError("未知回放操作。")


def sample_status(sample: dict[str, Any], threshold: float) -> tuple[str, ...]:
    """Describe strict threshold exceedance without assigning fault or safety labels."""
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("加速度阈值必须是有限的非负数。")
    return tuple(label for field, label in
                 (("lateral_accel", "横向加速度"), ("vertical_accel", "垂向加速度"))
                 if abs(sample[field]) > threshold)
