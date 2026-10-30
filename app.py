"""Local Streamlit entry point for RailVehicle-Lab."""

import streamlit as st

from rail_vehicle.db import init_db
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.pages import cockpit, ml_detection, run_analysis, vehicles, vibration_analysis
from rail_vehicle.pages import demo_center, fleet_comparison
from rail_vehicle.pages import home
from rail_vehicle.ui import render_navigation


def main() -> None:
    st.set_page_config(
        page_title="轨道车辆运行与动力学智能分析平台",
        page_icon="🚆",
        layout="wide",
    )
    public_demo = public_demo_enabled()
    if not public_demo:
        init_db()

    st.title("轨道车辆运行与动力学智能分析平台")
    st.caption("RAILVEHICLE LAB / 公开演示 · 仅使用虚构数据 · AI 服务已禁用" if public_demo else "RAILVEHICLE LAB / 本地运行与动力学分析工作空间")
    page = render_navigation(public_demo)
    if page != "动态运行驾驶舱":
        # Returning to the cockpit starts paused, never advances while hidden.
        st.session_state.pop(cockpit.STATE_KEY, None)
    if page == "平台首页":
        home.render()
    elif page == "车辆参数":
        vehicles.render()
    elif page == "CSV 数据导入":
        st.header("CSV 数据导入")
        st.caption("数据工作台 / 文件上传 → 格式校验 → 预览 → 保存")
        run_analysis._render_import()
    elif page == "运行分析":
        run_analysis.render()
    elif page == "动态运行驾驶舱":
        cockpit.render()
    elif page == "机器学习异常检测":
        ml_detection.render()
    elif page == "振动与平稳性分析":
        vibration_analysis.render()
    elif page == "多车辆运行对比":
        fleet_comparison.render()
    elif page == "统计与异常演示":
        run_analysis.render_statistics_demo()
    else:
        demo_center.render()


if __name__ == "__main__":
    main()
