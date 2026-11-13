"""Session-only competition walkthrough using existing fictional demonstrations."""

import importlib.util
import sys

import streamlit as st
from rail_vehicle.ui import navigate, page_title
from rail_vehicle.ml_anomaly import STATE_KEY as ML_STATE_KEY
from rail_vehicle.vibration import STATE_KEY as VIBRATION_STATE_KEY
from rail_vehicle.pages.cockpit import STATE_KEY as COCKPIT_STATE_KEY
from rail_vehicle.pages import bogie
from rail_vehicle.deployment import public_demo_enabled


DEMOS = {
    "cockpit": dict(page="动态运行驾驶舱", title="历史运行驾驶舱", dependency=None,
        description="展示速度仪表、双轴加速度、回放曲线与阈值提示。",
        steps="点击播放；可选 4× 倍速。第 30–32 秒观察横向超阈值提示，然后暂停与重置。",
        settings=dict(cockpit_source="内置虚构演示", cockpit_threshold=1.0, cockpit_rate=1.0),
        clear=(COCKPIT_STATE_KEY,)),
    "ml": dict(page="机器学习异常检测", title="机器学习与规则对照", dependency="sklearn",
        description="用独立的参考/检测样本，展示模型标记、规则标记及两者差异。",
        steps="点击“训练并检测”；查看四类数量、离群分数，再导出实验 PDF。分数不是故障概率。",
        settings=dict(ml_source="内置虚构实验", ml_contamination=0.05, ml_rule_threshold=1.0),
        clear=(ML_STATE_KEY, "experiment_ml_ai")),
    "vibration": dict(page="振动与平稳性分析", title="已知信号振动实验", dependency="scipy",
        description="100 Hz 虚构信号展示时域指标、PSD 主峰与滑动振动强度。",
        steps="点击“计算振动指标”；核对横向约 1.5 Hz、垂向约 6 Hz 主峰，再查看 18–20 秒幅值变化。",
        settings=dict(vibration_source="内置高频虚构信号", vibration_band_low=0.5,
                      vibration_band_high=20.0, vibration_window=1.0),
        clear=(VIBRATION_STATE_KEY, "experiment_vibration_ai")),
    "fleet": dict(page="多车辆运行对比", title="多车辆运行表现", dependency=None,
        description="对比两辆虚构车辆的速度、双轴 RMS、绝对峰值与采样范围。",
        steps="点击“计算多车辆对比”；调整共同速度区间并重新计算，解释可比性限制。",
        settings=dict(fleet_source="内置虚构对比"), clear=("fleet_comparison_result", "experiment_fleet_ai")),
    "statistics": dict(page="统计与异常演示", title="1 · 统计与异常回查", dependency=None,
        description="用两批虚构数据展示时间筛选、统计指标、异常区段和局部回查。",
        steps="点击“计算虚构运行分析”；选择批次、调整阈值、展开事件回查，再导出离线 PDF。",
        settings=dict(demo_analysis_threshold=1.0),
        clear=("statistics_demo_started", "demo_analysis_report_key", "demo_analysis_report_pdf")),
    "bogie": dict(page="三维转向架仿真", title="2 · 三维回放", dependency=None,
        description="用同一批虚构数据核对曲线、当前样本和模型提示。",
        steps="展开“定位超阈值区段”，选择第一段；核对横向读值与高亮部件。可回到教学动画对照正常运动。",
        settings=dict(bogie_source=bogie.DEMO, bogie_demo_batch=-1, bogie_threshold=1.0),
        clear=(bogie.STATE_KEY, bogie.FRAME_KEY, "bogie_batch_target", "bogie_evidence_target")),
    "evidence": dict(page="AI 智能分析中心", title="3 · 离线证据核查", dependency=None,
        description="查看 Python 计算的证据 ID、数值和口径，再跳回三维区段。",
        steps="选择异常证据后点击回放。此入口只展示证据，未调用 AI 服务。",
        settings=dict(ai_center_demo=True, ai_demo_batch=-1, ai_demo_threshold=1.0), clear=("ai_demo_evidence",)),
}


def launch_demo(name):
    """Button callback: set widget choices before the next app run, never compute."""
    demo = DEMOS[name]
    # Clear only demo-related session keys; never touch SQLite or user files.
    for key in demo["clear"]:
        st.session_state.pop(key, None)
    if name == "vibration":
        # Existing range widgets are keyed by source/selection. Reset only the
        # fictional range, retaining any historical range the user selected.
        for key in list(st.session_state):
            if key.startswith("vibration_range_") and "'source': 'demo'" in key:
                st.session_state.pop(key, None)
    if name == "statistics":
        for key in list(st.session_state):
            if (key.startswith("analysis_") and "_-1_-" in key) or key.startswith("demo_dynamics_"):
                st.session_state.pop(key, None)
    st.session_state.update(demo["settings"])
    st.session_state["navigation_page"] = demo["page"]


def render():
    page_title("演示中心")
    st.info("离线虚构数据演示 · 无需登记车辆或导入 CSV。进阶实验不自动计算；演示不调用 AI，也不写入数据库。")
    if public_demo_enabled():
        st.button("返回三步演示路线", key="demo_back_workbench", on_click=navigate, args=("平台首页",))
    else:
        render_walkthrough()
    st.subheader("进阶实验")
    for pair in (("cockpit", "ml"), ("vibration", "fleet")):
        for column, name in zip(st.columns(2), pair):
            _demo_card(column, name)
    with st.expander("运行环境"):
        st.caption("Python " + ".".join(map(str, sys.version_info[:3])))
        st.caption("机器学习：" + ("可用" if importlib.util.find_spec("sklearn") else "未安装"))
        st.caption("振动分析：" + ("可用" if importlib.util.find_spec("scipy") else "未安装"))
    with st.expander("答辩时应明确说明", expanded=False):
        st.write("Python 计算指标；规则描述超阈值样本；Isolation Forest 检测相对参考分布的离群点；AI 只解释已计算的聚合结果。")
        st.write("演示数据为虚构数据，已知信号只用于验证展示与计算；不代表真实车辆诊断准确率。振动指标未做标准频率加权，不提供舒适等级或运行安全结论。")
        st.write("实验 PDF 无需 AI 即可导出。公开演示模式禁用 AI 服务。" if public_demo_enabled() else "实验 PDF 无需 AI 即可导出。AI 需要单独配置且主动触发，真实服务可能产生费用；演示入口不会更改或启用 AI 配置。")


def render_walkthrough():
    st.subheader("三步演示路线")
    st.caption("先确认异常样本，再对照模型提示，最后核查证据口径。每一步都可以单独打开。")
    for column, name in zip(st.columns(3), ("statistics", "bogie", "evidence")):
        _demo_card(column, name)


def _demo_card(column, name):
    demo = DEMOS[name]
    with column.container(border=True):
        st.subheader(demo["title"])
        st.write(demo["description"])
        st.caption(demo["steps"])
        available = demo["dependency"] is None or importlib.util.find_spec(demo["dependency"]) is not None
        if not available:
            st.warning("可选依赖未安装，请按 README 安装相应可选依赖后重启应用。")
        st.button("打开演示", key=f"launch_demo_{name}", on_click=launch_demo,
                  args=(name,), disabled=not available, width="stretch")
