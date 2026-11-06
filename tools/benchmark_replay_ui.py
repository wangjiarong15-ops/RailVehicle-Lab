"""Exercise high-density fictional events with Streamlit's local test runner.

Run from the repository root: python -m tools.benchmark_replay_ui
Not a browser, concurrent-user or SQLite benchmark; writes no user data.
"""
import argparse
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from rail_vehicle.pages import cockpit
from rail_vehicle.replay import prepare_replay
from tools.benchmark_replay import windows_memory_mb


def cockpit_fixture():
    from rail_vehicle.pages.cockpit import render
    render()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300000)
    args = parser.parse_args()
    if args.count < 202:
        parser.error("为覆盖至少两页事件，样本数必须不少于 202")
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [dict(timestamp=(origin + timedelta(milliseconds=10 * i)).isoformat(),
                 speed_kmh=60., lateral_accel=2. if i % 2 == 0 else .1,
                 vertical_accel=.2) for i in range(args.count)]
    track = prepare_replay(rows)
    expected_events = (args.count + 1) // 2
    last_page = (expected_events + cockpit.EVENTS_PER_PAGE - 1) // cockpit.EVENTS_PER_PAGE
    with patch.object(cockpit, "_demo_track", return_value=track), \
         patch.object(cockpit, "list_vehicles", return_value=[]), \
         patch.object(cockpit, "monotonic", return_value=100.0):
        app = AppTest.from_function(cockpit_fixture, default_timeout=120).run()
        start = time.perf_counter()
        app.radio(key="cockpit_source").set_value("内置虚构演示").run(timeout=120)
        initial = time.perf_counter() - start
        if app.exception:
            raise RuntimeError("虚构数据首次页面运行失败")
        first_options = len(app.selectbox(key="cockpit_event").options)
        start = time.perf_counter()
        app.number_input(key="cockpit_event_page").set_value(last_page).run(timeout=120)
        page_time = time.perf_counter() - start
        last_options = len(app.selectbox(key="cockpit_event").options)
        app.selectbox(key="cockpit_event").set_value(expected_events - 1).run(timeout=120)
        start = time.perf_counter()
        app.button(key="cockpit_event_jump").click().run(timeout=120)
        jump_time = time.perf_counter() - start
        expected_position = track.offsets[(expected_events - 1) * 2]
        if (app.exception or first_options > cockpit.EVENTS_PER_PAGE or
                last_options > cockpit.EVENTS_PER_PAGE or
                app.session_state[cockpit.STATE_KEY].position != expected_position):
            raise RuntimeError("分页数量或最后一个事件的定位不正确")
        print(f"虚构样本: {args.count:,}；事件: {expected_events:,}；页数: {last_page:,}")
        print(f"首次页面运行: {initial:.3f} s；切换末页运行: {page_time:.3f} s；末事件跳转运行: {jump_time:.3f} s")
        print(f"首/末页选项: {first_options}/{last_options}；末事件定位: 通过")
        memory = windows_memory_mb()
        if memory:
            print(f"本进程工作集 / 累计峰值: {memory[0]:.1f} / {memory[1]:.1f} MiB")


if __name__ == "__main__":
    main()
