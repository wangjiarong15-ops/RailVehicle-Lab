"""Map observed signals to explicitly illustrative bogie scenes."""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from math import isfinite, tau
from statistics import median

from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.replay import ReplayState, ReplayTrack, control_replay, replay_long_gap_at


ILLUSTRATION_PERIOD = 2.4
EVENT_FILTERS = {
    "axes": "横向与垂向（分轴统计）",
    "lateral": "横向超阈值",
    "vertical": "垂向超阈值",
    "combined": "双轴同点超阈值",
}


@dataclass(frozen=True)
class Scene:
    label: str
    mode: str
    parts: tuple[str, ...]
    impact: str
    check: str


NORMAL = Scene("未超阈值", "正常运行", (), "当前样本的两个加速度通道均未超阈值。", "继续核查整段曲线。")
LATERAL = Scene("横向加速度超阈值", "横向振动", ("构架", "一系悬挂"),
                "横向信号超过设定范围；高亮表示相关结构示意。", "检查传感器方向、固定和相邻样本。")
VERTICAL = Scene("垂向加速度超阈值", "垂向振动", ("一系悬挂", "二系悬挂"),
                 "垂向信号超过设定范围；高亮表示相关结构示意。", "核对采样质量和运行工况。")
COMBINED = Scene("综合振动异常", "异常运行", ("构架", "一系悬挂", "二系悬挂"),
                 "同一个样本的横向和垂向通道均超阈值。", "比对两个通道的原始波形与采样时刻。")
GAP = Scene("长采样间隔 · 无当时观测", "正常运行", (),
            "仪表保留间隔前的最后一次观测，模型暂停在中立示意。",
            "定位到间隔前后的观测点，核对采集记录。")
TEACHING = {
    "正常运行": NORMAL,
    "垂向振动": VERTICAL,
    "横向振动": LATERAL,
    "蛇行运动示意": Scene("蛇行运动示意", "蛇行运动示意", ("构架", "轮对"),
                           "横移与摇头叠加。", "实际判断需多点同步测量及动力学验证。"),
    "异常运行": COMBINED,
    "一系悬挂异常示意": Scene("一系悬挂异常示意", "垂向振动", ("一系悬挂",),
                               "示意构架与轮对之间的相对运动。", "检查弹簧、减振器及连接状态。"),
    "轴承异常示意": Scene("轴承异常示意", "正常运行", ("轴箱",),
                           "标出轴箱内轴承的示意位置。", "核对温度、振动和维护记录。"),
}


def scene_for_sample(sample, threshold):
    lateral = abs(sample["lateral_accel"]) > threshold
    vertical = abs(sample["vertical_accel"]) > threshold
    return {(True, True): COMBINED, (True, False): LATERAL,
            (False, True): VERTICAL, (False, False): NORMAL}[lateral, vertical]


def event_positions(track: ReplayTrack, threshold: float):
    """Use the existing detector; retain the first row at each channel event start."""
    events = detect_anomalies(track.samples, threshold)["events"]
    by_field = defaultdict(deque)
    preceding = {}
    for index, row in enumerate(track.samples):
        for field in ("lateral_accel", "vertical_accel"):
            active = abs(row[field]) > threshold
            if active and not preceding.get(field):
                by_field[(field, row["timestamp"])].append(index)
            preceding[field] = active
    return [(event, by_field[(event["field"], event["start_timestamp"])].popleft()) for event in events]


@dataclass(frozen=True)
class EventSpanIndex:
    """Index detected runs by row, preserving distinct samples with the same timestamp."""

    channels: tuple

    @classmethod
    def from_events(cls, events):
        by_field = {"lateral_accel": [], "vertical_accel": []}
        for event, start in events:
            by_field[event["field"]].append((start, start + event["sample_count"] - 1))
        return cls(tuple((field, tuple(spans), tuple(end for _, end in spans))
                         for field, spans in by_field.items()))

    def window(self, offsets, first, last, limit):
        """Clip spans to already displayed rows, never extend a band to future samples."""
        visible = []
        for field, spans, ends in self.channels:
            begin = bisect_left(ends, first)
            for start, end in spans[begin:begin + limit]:
                if start > last:
                    break
                visible.append((offsets[max(first, start)], field, offsets[min(last, end)]))
        # Python's stable sort preserves chronological row order for duplicate timestamps.
        visible.sort(key=lambda span: (span[0], span[1]))
        return tuple((field, start, end) for start, field, end in visible[:limit])

    def simultaneous_spans(self):
        """Intersect row spans, so unrelated samples sharing a timestamp stay separate."""
        lateral, vertical = (channel[1] for channel in self.channels)
        i = j = 0
        while i < len(lateral) and j < len(vertical):
            left, right = lateral[i], vertical[j]
            start, end = max(left[0], right[0]), min(left[1], right[1])
            if start <= end:
                yield start, end
            if left[1] <= right[1]:
                i += 1
            if right[1] <= left[1]:
                j += 1


@dataclass(frozen=True)
class ReplayEventList:
    entries: tuple
    starts: tuple[int, ...]

    @classmethod
    def from_entries(cls, entries):
        ordered = tuple(sorted(entries, key=lambda item: (item[1], item[0]["field"])))
        return cls(ordered, tuple(row for _, row in ordered))

    def neighbors(self, current_row):
        """Skip duplicate start rows across channels; never wrap to another end of the batch."""
        previous = bisect_left(self.starts, current_row) - 1
        following = bisect_right(self.starts, current_row)
        if previous >= 0:
            previous = bisect_left(self.starts, self.starts[previous])
        return (previous if previous >= 0 else None,
                following if following < len(self.entries) else None)


def replay_event_catalog(track, events, index):
    """Add a distinct simultaneous-observation view without changing the per-axis detector."""
    grouped = {"axes": events, "lateral": [], "vertical": [], "combined": []}
    for event, start in events:
        key = "lateral" if event["field"] == "lateral_accel" else "vertical"
        grouped[key].append((event, start))
    for start, end in index.simultaneous_spans():
        peaks = {}
        for field in ("lateral_accel", "vertical_accel"):
            peaks[field] = max((track.samples[row][field] for row in range(start, end + 1)), key=abs)
        grouped["combined"].append((dict(type=EVENT_FILTERS["combined"], field="combined",
                                         start_timestamp=track.samples[start]["timestamp"],
                                         end_timestamp=track.samples[end]["timestamp"],
                                         duration_seconds=track.offsets[end]-track.offsets[start],
                                         sample_count=end-start+1, peaks=peaks), start))
    return {key: ReplayEventList.from_entries(entries) for key, entries in grouped.items()}


@dataclass(frozen=True)
class EventLoop:
    label: str
    start: float
    end: float
    first_row: int
    last_row: int

    @property
    def timed(self):
        return self.end > self.start

    def index_at(self, track, position):
        if position <= self.start:
            return self.first_row
        return max(self.first_row, min(self.last_row, track.index_at(position)))


def event_loop(track, event, first_row, before, after):
    """Use observed endpoints; a zero-duration window remains available by row."""
    if not all(isfinite(margin) and margin >= 0 for margin in (before, after)):
        raise ValueError("区段前后的时间必须为非负数。")
    last_row = first_row + event["sample_count"] - 1
    start = max(0., track.offsets[first_row] - before)
    end = min(track.duration, track.offsets[last_row] + after)
    first = bisect_left(track.offsets, start) if before else first_row
    last = bisect_right(track.offsets, end) - 1 if after else last_row
    return EventLoop(event["type"], track.offsets[first], track.offsets[last], first, last)


def advance_event_loop(state, now, window, *, wrap=True):
    if not window.timed:
        return replace(state, position=window.start, playing=False, last_tick=None)
    if not state.playing:
        return state
    if state.last_tick is None:
        return replace(state, last_tick=now)
    # Show the ending sample before wrapping, even when the playback tick overshoots it.
    if wrap and state.position >= window.end:
        position = window.start
    else:
        position = min(window.end, max(window.start, state.position) + max(0., now - state.last_tick) * state.rate)
    return replace(state, position=position, last_tick=now)


def control_event_loop(state, action, now, window, *, rate=None):
    if action == "reset":
        return ReplayState(position=window.start, rate=state.rate)
    state = advance_event_loop(state, now, window, wrap=False)
    relative = replace(state, position=max(0., min(window.end, state.position) - window.start))
    updated = control_replay(relative, action, now, window.end-window.start, rate=rate)
    if action == "play" and updated.playing:
        updated = replace(updated, last_tick=None)
    # The generic controller stops at the end; loop rate changes keep playback running.
    if action == "rate" and state.playing:
        updated = replace(updated, playing=True, last_tick=now)
    return replace(updated, position=min(window.end, window.start + updated.position))


def advance_teaching(state, now):
    """Advance the repeating illustration without stopping at a cycle boundary."""
    if not state.playing:
        return state
    if state.last_tick is None:
        return replace(state, last_tick=now)
    tick = max(state.last_tick, now)
    elapsed = (tick - state.last_tick) * state.rate
    return replace(state, position=(state.position + elapsed) % ILLUSTRATION_PERIOD, last_tick=tick)


def control_teaching(state, action, now, *, rate=None):
    state = advance_teaching(state, now)
    if action == "play":
        return state if state.playing else replace(state, playing=True, last_tick=now)
    if action == "pause":
        return replace(state, playing=False, last_tick=None)
    if action == "reset":
        return ReplayState(rate=state.rate)
    if action == "rate":
        if rate is None or not isfinite(rate) or rate <= 0:
            raise ValueError("回放倍速必须是有限的正数。")
        return replace(state, rate=rate)
    raise ValueError("未知回放操作。")


def gap_cutoff(track: ReplayTrack):
    intervals = [end - start for start, end in zip(track.offsets, track.offsets[1:]) if end > start]
    return 3 * median(intervals) if intervals else None


def replay_scene(track: ReplayTrack, position, index, threshold, cutoff):
    if replay_long_gap_at(track, position, cutoff):
        return GAP
    return scene_for_sample(track.samples[index], threshold)


def illustration_phase(position):
    """Fixed display cycle; measured speed and acceleration do not set displacement."""
    return position * tau / ILLUSTRATION_PERIOD
