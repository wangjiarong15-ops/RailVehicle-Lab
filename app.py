"""Local Streamlit entry point for RailVehicle-Lab."""

import streamlit as st

from rail_vehicle.db import init_db
from rail_vehicle.pages import cockpit, ml_detection, run_analysis, vehicles, vibration_analysis
from rail_vehicle.pages import fleet_comparison


def main() -> None:
    st.set_page_config(
        page_title="轨道车辆运行与动力学智能分析平台",
        page_icon="🚆",
        layout="wide",
    )
    init_db()

    st.title("轨道车辆运行与动力学智能分析平台")
    st.caption("MVP · 本地运行")

    page = st.sidebar.radio("功能导航", ["车辆参数", "运行分析", "动态运行驾驶舱", "机器学习异常检测", "振动与平稳性分析", "多车辆运行对比"])
    if page != "动态运行驾驶舱":
        # Returning to the cockpit starts paused, never advances while hidden.
        st.session_state.pop(cockpit.STATE_KEY, None)
    if page == "车辆参数":
        vehicles.render()
    elif page == "运行分析":
        run_analysis.render()
    elif page == "动态运行驾驶舱":
        cockpit.render()
    elif page == "机器学习异常检测":
        ml_detection.render()
    elif page == "振动与平稳性分析":
        vibration_analysis.render()
    else:
        fleet_comparison.render()


if __name__ == "__main__":
    main()
