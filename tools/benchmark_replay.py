"""Repeatable synthetic replay benchmark; never opens a database or calls AI.

Run from the repository root: python -m tools.benchmark_replay
"""
import argparse
import ctypes
import gc
import os
import time
from datetime import datetime, timedelta, timezone

from rail_vehicle.pages.cockpit import _cached_events, _cached_sampling_review
from rail_vehicle.replay import prepare_replay


def windows_memory_mb():
    """Return this process's current/peak working set, when supported."""
    if os.name != "nt":
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    read = ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    if not read(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.WorkingSetSize / 1048576, counters.PeakWorkingSetSize / 1048576


def timed(label, function):
    start = time.perf_counter()
    result = function()
    print(f"{label}: {time.perf_counter() - start:.3f} s", flush=True)
    return result


def run(count, repeats, pattern="sparse"):
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [dict(timestamp=(origin + timedelta(milliseconds=10 * i)).isoformat(),
                 speed_kmh=60., lateral_accel=2. if i % (2 if pattern == "alternating" else 1000) == 0 else .1,
                 vertical_accel=.2) for i in range(count)]
    print(f"虚构样本: {count:,}；异常分布: {pattern}")
    track = timed("准备回放", lambda: prepare_replay(rows))
    state = {}
    events = timed("首次异常检查", lambda: _cached_events(state, track, 1.0))
    review = timed("首次采样检查", lambda: _cached_sampling_review(state, track, 0.0, 3.0))

    def repeat_checks():
        for _ in range(repeats):
            _cached_events(state, track, 1.0)
            _cached_sampling_review(state, track, 0.0, 3.0)
    timed(f"{repeats:,} 次缓存命中检查", repeat_checks)
    print(f"异常事件: {len(events):,}；长间隔: {review['long_gap_count']:,}")
    memory = windows_memory_mb()
    if memory:
        print(f"本进程工作集 / 累计峰值: {memory[0]:.1f} / {memory[1]:.1f} MiB")
    del track, state, events, review, rows
    gc.collect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", type=int, nargs="+", default=[100000, 300000])
    parser.add_argument("--repeats", type=int, default=10000)
    parser.add_argument("--pattern", choices=("sparse", "alternating"), default="sparse",
                        help="alternating 为每隔一个样本超阈值的高密度场景")
    args = parser.parse_args()
    if any(count <= 0 for count in args.counts) or args.repeats <= 0:
        parser.error("样本数和重复次数必须为正整数")
    for count in args.counts:
        run(count, args.repeats, args.pattern)


if __name__ == "__main__":
    main()
