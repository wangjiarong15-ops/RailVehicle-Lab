"""Parameter controls for the independent vertical response calculation."""
from dataclasses import replace

import streamlit as st

from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.replay import ReplayState
from rail_vehicle.suspension import SCENARIOS, SuspensionCase, simulate


@st.cache_data(max_entries=16, show_spinner=False)
def _calculate(case):
    result = simulate(case)
    if case.scenario == "soft":
        result["nominal_travel_m"] = simulate(replace(case, scenario="rough"))["channels"]["travel_m"]
    return result


def render_lab(main):
    scenario = st.selectbox("计算工况", list(SCENARIOS), index=1, format_func=SCENARIOS.get, key="sim_case")
    speed = st.slider("初速（m/s）", 1, 10, 6)
    with st.expander("悬挂参数"):
        mass = st.slider("等效质量（kg）", 500, 2500, 1200, 50)
        stiffness = st.slider("名义刚度（kN/m）", 100, 1000, 450, 25)
        damping = st.slider("阻尼（kN·s/m）", 1, 40, 10)
        amplitude = st.slider("激励幅值（mm）", 0, 8, 4, disabled=scenario not in ("rough", "flat", "soft"))
    case = SuspensionCase(scenario, mass, stiffness*1000, damping*1000, speed, amplitude/1000)
    result = _calculate(case)
    with main:
        st.subheader("悬挂响应试验")
        st.caption(f"单自由度垂向模型 · 有效刚度 {case.stiffness/1000:g} kN/m · 无阻尼固有频率 {result['natural_hz']:.2f} Hz")
        notes = {
            "rough": "支承输入为波长 3 m 的正弦波。",
            "soft": "名义刚度降低 50%，与周期不平顺工况采用相同输入。",
            "flat": "每转施加一次平滑下凹的等效支承输入，仅代表周期激励。",
            "brake": "设定四秒匀减速至停车，按行程计算轮轴转角。",
            "curve": "在局部切线视角展示预设的过渡横移，轨道几何保持平直。",
            "hunting": "横移与摇头使用预设相位关系。",
            "level": "平直轨道、零垂向输入，轮轴按设定初速滚动。",
        }
        if webgl_available():
            render_webgl(mode="正常运行", phase=0, state=ReplayState(), visible=PARTS,
                         highlight=(), view="整体视图", focus=None, labels=False, rails=True,
                         exploded=False, spread=0, camera_revision=0, continuous=False, height=840,
                         key="suspension_model", simulation=result)
        else:
            st.info("三维资源不完整，可展开下方数值样本查看计算结果。")
        st.caption(notes[scenario] + "模型以轮座为参考，构架按计算行程移动，一系弹簧与减振器跟随端点。"
                   "参数为教学假设，横移、摇头和轮转为运动学示意。")
        with st.expander("计算方法与数值样本"):
            st.latex(r"m\ddot z+c(\dot z-\dot y)+k(z-y)=0")
            st.caption("y 为给定支承位移，z 为等效构架位移，初始位移和速度均为零。四阶 Runge–Kutta 积分步长 1 ms，每 10 ms 输出一次。")
            c = result["channels"]
            st.dataframe({"时间（s）": [i/result["sample_hz"] for i in range(len(c["frame_m"]))],
                          "支承（m）": c["support_m"], "构架（m）": c["frame_m"],
                          "悬挂行程（m）": c["travel_m"], "构架加速度（m/s²）": c["accel_m_s2"]},
                         hide_index=True)
