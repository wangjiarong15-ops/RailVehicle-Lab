"""Runnable lateral/roll experiment with a shared 3D and response cursor."""

from dataclasses import asdict, replace
import json

import streamlit as st

from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.lateral_dynamics import (LateralCase, TRACKS, case_config, lateral_playback, lateral_summary,
                                          read_case, simulate_lateral)
from rail_vehicle.replay import ReplayState
from rail_vehicle.ui import COPILOT, navigate


@st.cache_data(show_spinner=False, max_entries=12)
def _run(case):
    return simulate_lateral(case)


def _save(content):
    return json.dumps(content, ensure_ascii=False, allow_nan=False)


def render_lateral_lab(main):
    state = st.session_state
    active = state.get("lateral_experiment", LateralCase())
    loaded = st.file_uploader("载入横向工况 JSON", type=["json"], max_upload_size=1)
    if loaded is not None and st.button("应用横向工况"):
        try:
            active = read_case(loaded.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            state.lateral_experiment = active
            state.lateral_form_revision = state.get("lateral_form_revision", 0)+1
            st.rerun()
    with st.form(f"lateral_experiment_{state.get('lateral_form_revision', 0)}"):
        track = st.selectbox("横向激励", TRACKS, index=list(TRACKS).index(active.track), format_func=TRACKS.get)
        speed = st.number_input("运行速度（m/s）", 2., 60., float(active.speed_m_s))
        amplitude = st.number_input("支承输入幅值（mm）", 0., 20., 1000*active.amplitude_m)
        wavelength = st.number_input("波长或局部长度（m）", 2., 60., float(active.wavelength_m))
        duration = st.number_input("仿真时间（s）", 1, 15, active.duration_s)
        with st.expander("质量、二系悬挂和抗侧滚参数"):
            mass = st.number_input("等效质量（kg）", 3000., 20000., float(active.mass_kg))
            inertia = st.number_input("侧滚惯量（kg·m²）", 3000., 45000., float(active.roll_inertia_kg_m2))
            height = st.number_input("质心到支承平面的高度（m）", .3, 1.5, float(active.com_height_m))
            ky = st.number_input("二系总横向刚度（kN/m）", 60., 800., active.lateral_k_n_m/1000)
            cy = st.number_input("二系总横向阻尼（kN·s/m）", 2., 60., active.lateral_c_ns_m/1000)
            kz = st.number_input("单侧垂向刚度（kN/m）", 60., 600., active.vertical_k_each_n_m/1000)
            cz = st.number_input("单侧垂向阻尼（kN·s/m）", 1., 50., active.vertical_c_each_ns_m/1000)
            kr = st.number_input("等效抗侧滚刚度（kN·m/rad）", 0., 600., active.anti_roll_k_nm_rad/1000)
        if st.form_submit_button("计算横向与侧滚响应", type="primary"):
            active = LateralCase(track=track, speed_m_s=speed, amplitude_m=amplitude/1000,
                                 wavelength_m=wavelength, duration_s=duration, mass_kg=mass,
                                 roll_inertia_kg_m2=inertia, com_height_m=height, lateral_k_n_m=ky*1000,
                                 lateral_c_ns_m=cy*1000, vertical_k_each_n_m=kz*1000,
                                 vertical_c_each_ns_m=cz*1000, anti_roll_k_nm_rad=kr*1000)
            state.lateral_experiment = active
    st.download_button("保存横向工况 JSON", _save(case_config(active)), "lateral-experiment.json",
                       "application/json", on_click="ignore")
    st.button("将此工况带入 Copilot", on_click=navigate, args=(COPILOT,),
              kwargs={"cp_lateral_case": active}, icon=":material/forum:")
    with main:
        st.subheader("车体横移与侧滚试验")
        st.caption("2 自由度 · 教学参数 · 转向架横向支承输入随行驶距离变化 · 初始状态取静平衡")
        result = _run(active)
        facts = lateral_summary(result)
        columns = st.columns(3)
        columns[0].metric("车体横移绝对峰值", f"{facts['body_y_peak_mm']:.3f} mm")
        columns[1].metric("侧滚绝对峰值", f"{facts['roll_peak_mrad']:.3f} mrad")
        columns[2].metric("横向加速度 RMS", f"{facts['body_y_rms_m_s2']:.4f} m/s²")
        gain = st.radio("动画显示倍率", (1, 2, 4), format_func=lambda value: f"{value}×", horizontal=True)
        if webgl_available():
            render_webgl(mode="正常运行", phase=0, state=ReplayState(), visible=PARTS, highlight=(),
                         view="整体视图", focus=None, labels=False, rails=True, exploded=False, spread=0,
                         camera_revision=0, continuous=False, height=730, key="lateral_suspension_model",
                         simulation=lateral_playback(result, gain))
        st.caption("车体安装面以示意板表达。横移、侧滚及空气弹簧端点共用样本时钟。"
                   "视窗按同一倍率放大并限制动作幅值，参数与读数保持计算原值。")
        _response_tables(result)
        with st.expander("工况对照与局部敏感性"):
            st.button("将当前已计算工况设为对照基准", on_click=lambda: state.update(lateral_reference=active))
            if reference := state.get("lateral_reference"):
                st.dataframe([{"工况": "基准", **lateral_summary(_run(reference))}, {"工况": "当前", **facts}],
                             hide_index=True)
            if st.button("运行 ±5% 单因素敏感性"):
                st.dataframe(_sensitivity(active), hide_index=True)
        with st.expander("计算方程、模态与证据"):
            st.latex(r"M\ddot q+C\dot q+Kq=d(k_y y_b+c_y\dot y_b),\quad q=[y,\phi_x]^T,\quad d=[1,h]^T")
            st.caption("平均加速度 Newmark 积分步长 1 ms，每 5 ms 输出。转向架输入为规定运动，"
                       "忽略轮轨横向力、构架侧滚、横摆及非线性。抗侧滚使用等效力矩刚度。")
            st.dataframe({"固有频率（Hz）": result["modes"]["undamped_hz"],
                          "投影阻尼比": result["modes"]["modal_projected_damping_ratio"]}, hide_index=True)
            st.caption(result["evidence_id"])
            st.download_button("导出横向计算结果 JSON", _save(result), "lateral-response.json",
                               "application/json", on_click="ignore")


def _response_tables(result):
    time_key = "时间（s）"
    with st.expander("位移、速度与加速度"):
        channels = {"位移": ("q", "车体横移（mm）", "侧滚角（mrad）", 1000),
                    "速度": ("dq", "横向速度（m/s）", "侧滚角速度（rad/s）", 1),
                    "加速度": ("ddq", "横向加速度（m/s²）", "侧滚角加速度（rad/s²）", 1)}
        channel, shift, roll, gain = channels[st.selectbox("响应量", channels)]
        for index, (column, label) in enumerate(zip(st.columns(2), (shift, roll), strict=True)):
            column.line_chart({time_key: result["time_s"], label: [gain*row[index] for row in result[channel]]},
                              x=time_key, y=label)


def _sensitivity(case):
    rows = []
    parameters = asdict(case)
    for field, label in (("lateral_k_n_m", "横向刚度"), ("lateral_c_ns_m", "横向阻尼"),
                         ("anti_roll_k_nm_rad", "抗侧滚刚度")):
        if not parameters[field]:
            rows.append({"参数": label, "状态": "零值无相对扰动"})
            continue
        try:
            low, high = (lateral_summary(_run(replace(case, **{field: parameters[field]*factor})))
                         for factor in (.95, 1.05))
        except ValueError:
            rows.append({"参数": label, "状态": "扰动超出教学参数范围"})
        else:
            rows.append({"参数": label, "状态": "已计算", "横向 RMS 对参数相对变化量的斜率":
                         (high["body_y_rms_m_s2"]-low["body_y_rms_m_s2"])/.10,
                         "侧滚峰值对参数相对变化量的斜率（mrad）":
                         (high["roll_peak_mrad"]-low["roll_peak_mrad"])/.10})
    return rows
