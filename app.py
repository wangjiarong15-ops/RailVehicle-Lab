"""Local Streamlit entry point for RailVehicle-Lab."""

import streamlit as st
# Streamlit may interrupt a running script when a user changes page.  Finish
# pandas' lazy initialization before rendering navigation: Plotly otherwise can
# see a partially initialized module on the next run.  pandas is a Streamlit
# runtime dependency; no user data is loaded here.
import pandas  # noqa: F401

from rail_vehicle.db import init_db
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.pages import cockpit, ml_detection, run_analysis, vehicles, vibration_analysis
from rail_vehicle.pages import demo_center, fleet_comparison
from rail_vehicle.pages import home
from rail_vehicle.pages import ai_center
from rail_vehicle.pages import copilot
from rail_vehicle.pages import bogie
from rail_vehicle.pages.suspension_lab import render_lab
from rail_vehicle.pages.data_workflow import clear_workbench_navigation, render_workbench_return, select_workspace_page
from rail_vehicle.workspace_resume import finish_workspace, render_resume_notice, restore_workspace
from rail_vehicle.ui import (
    ANALYSIS, ANOMALIES, COMPARISON, COPILOT, LINKED, SIMULATION, REPORT, apply_design, initialize_theme, page_title, render_navigation,
)


def main() -> None:
    st.set_page_config(
        page_title="轨道车辆异常分析与三维可视化平台",
        page_icon="🚆",
        layout="wide",
        initial_sidebar_state="auto",
    )
    public_demo = public_demo_enabled()
    if not public_demo:
        init_db()

    apply_design()
    resume_slot = st.empty()
    restore_workspace(resume_slot, public=public_demo)
    page = render_navigation(public_demo, on_select=select_workspace_page)
    if page != COPILOT:
        copilot.interrupt()
    render_resume_notice(page)
    clear_workbench_navigation("三维转向架仿真" if page == LINKED else page)
    if page != "动态运行驾驶舱":
        # Returning to the cockpit starts paused, never advances while hidden.
        st.session_state.pop(cockpit.STATE_KEY, None)
    if page not in ("三维转向架仿真", LINKED):
        st.session_state.pop(bogie.STATE_KEY, None)
        st.session_state.pop(bogie.FRAME_KEY, None)
        st.session_state.pop(bogie.REVIEW_KEY, None)
    if page == "平台首页":
        home.render()
    elif page == "车辆参数":
        vehicles.render()
    elif page == "CSV 数据导入":
        page_title("CSV 数据导入")
        run_analysis._render_import()
    elif page in (ANALYSIS, ANOMALIES, REPORT):
        if public_demo:
            run_analysis.render_statistics_demo(view=page)
        else:
            run_analysis.render(view=page)
    elif page == COMPARISON:
        run_analysis.render_comparison()
    elif page == "动态运行驾驶舱":
        cockpit.render()
    elif page in ("三维转向架仿真", LINKED):
        bogie.render(linked=page == LINKED)
    elif page == SIMULATION:
        page_title("动力学仿真", "给定轨道激励，比较构架与悬挂的计算响应。")
        render_workbench_return("simulation_return_analysis")
        if st.session_state.get("sim_case") == "coupled":
            model, parameters = st.columns([4, 1.45])
        else:
            parameters, model = st.columns([1, 4.5])
        with parameters:
            render_lab(model)
    elif page == "机器学习异常检测":
        ml_detection.render()
    elif page == "振动与平稳性分析":
        vibration_analysis.render()
    elif page == "多车辆运行对比":
        fleet_comparison.render()
    elif page == "AI 智能分析中心":
        ai_center.render()
    elif page == COPILOT:
        copilot.render(public_demo)
    elif page == "统计与异常演示":
        run_analysis.render_statistics_demo()
    elif page == "演示中心":
        demo_center.render()
    finish_workspace(resume_slot, page, public=public_demo)
    initialize_theme()


if __name__ == "__main__":
    main()
