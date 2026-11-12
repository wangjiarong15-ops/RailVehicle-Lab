"""Map observed signals to explicitly illustrative bogie scenes."""

from collections import defaultdict, deque
from dataclasses import dataclass
from math import tau
from statistics import median

from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.replay import ReplayTrack, replay_long_gap_at


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


def gap_cutoff(track: ReplayTrack):
    intervals = [end - start for start, end in zip(track.offsets, track.offsets[1:]) if end > start]
    return 3 * median(intervals) if intervals else None


def replay_scene(track: ReplayTrack, position, index, threshold, cutoff):
    if replay_long_gap_at(track, position, cutoff):
        return GAP
    return scene_for_sample(track.samples[index], threshold)


def illustration_phase(position):
    """Fixed display cycle; measured speed and acceleration do not set displacement."""
    return position * tau / 2.4
