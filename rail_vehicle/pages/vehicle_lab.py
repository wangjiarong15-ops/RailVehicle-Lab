"""Full-vehicle experiment controls and teaching results."""

from dataclasses import replace
import json

import streamlit as st

from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.replay import ReplayState
from rail_vehicle.verification_residuals import COLUMNS, compare_vehicle_reference
from rail_vehicle.vehicle_dynamics import (
    TRACKS, VehicleCase, bogie_playback, experiment_config, local_sensitivity, read_experiment, simulate_vehicle, summary,
)


@st.cache_data(max_entries=12, show_spinner=False)
def _run(case):
    return simulate_vehicle(case)


def render_vehicle_lab(main):
    state = st.session_state
    active = state.get("vehicle_experiment", VehicleCase())
    upload = st.file_uploader("载入工况 JSON", type=["json"], max_upload_size=1)
    if upload is not None and st.button("应用已载入工况"):
        try:
            state.vehicle_experiment = read_experiment(upload.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            state.vehicle_form_revision = state.get("vehicle_form_revision", 0)+1
            st.rerun()
    with st.form(f"vehicle_experiment_{state.get('vehicle_form_revision', 0)}"):
        track = st.selectbox("轨道激励", TRACKS, index=list(TRACKS).index(active.track), format_func=TRACKS.get)
        speed = st.number_input("速度（m/s）", 2., 70., float(active.speed_m_s))
        amplitude = st.number_input("轨道幅值（mm）", 0., 12., active.amplitude_m*1000)
        wavelength = st.number_input("波长或凸起长度（m）", 2., 40., float(active.wavelength_m))
        duration = st.number_input("时长（s）", 1, 20, active.duration_s)
        seed = st.number_input("随机种子", 0, 2**32-1, active.seed)
        with st.expander("教学悬挂参数"):
            p = active.vehicle
            k1 = st.number_input("一系刚度（kN/m）", 200., 3000., p.primary_k_n_m/1000)
            c1 = st.number_input("一系阻尼（kN·s/m）", 1., 80., p.primary_c_ns_m/1000)
            k2 = st.number_input("二系刚度（kN/m）", 100., 3000., p.secondary_k_n_m/1000)
            c2 = st.number_input("二系阻尼（kN·s/m）", 1., 150., p.secondary_c_ns_m/1000)
        if st.form_submit_button("计算整车响应"):
            active = VehicleCase(replace(p, primary_k_n_m=k1*1000, primary_c_ns_m=c1*1000,
                                         secondary_k_n_m=k2*1000, secondary_c_ns_m=c2*1000),
                                 track, speed, amplitude/1000, wavelength, duration, seed)
            state.vehicle_experiment = active
    st.download_button("保存工况 JSON", json.dumps(experiment_config(active), ensure_ascii=False),
                       file_name="vehicle-experiment.json", mime="application/json", on_click="ignore")
    with main:
        st.subheader("整车垂向与点头试验")
        st.caption("10 自由度 · 教学参数 · 两台转向架与四组轮对 · 以静平衡为零")
        result = _run(active)
        if result["contact_assumption_exceeded"]:
            st.warning("线性估计出现非正轮轨载荷，持续接触假设超出适用范围。请降低幅值或速度后比较。")
        st.caption(f"{TRACKS[active.track]} · {active.speed_m_s:g} m/s · {active.amplitude_m*1000:g} mm · "
                   f"四轴延迟 {' / '.join(f'{value:.3f}' for value in result['axle_delay_s'])} s")
        control = st.columns(2)
        bogie = control[0].radio("三维观察转向架", ("front", "rear"), horizontal=True,
                                 format_func={"front": "前转向架", "rear": "后转向架"}.get)
        gain = control[1].radio("三维显示倍率", (1, 2, 4), horizontal=True, format_func=lambda n: f"{n}×")
        if webgl_available():
            render_webgl(mode="正常运行", phase=0, state=ReplayState(), visible=PARTS, highlight=(),
                         view="整体视图", focus=None, labels=False, rails=False, exploded=False, spread=0,
                         camera_revision=0, continuous=False, height=670, key="vehicle_suspension_model",
                         simulation=bogie_playback(result, bogie, gain))
        st.caption("GLB 视窗按统一倍率更新单台转向架的构架、轮对和一系连接。二系行程可点选空气弹簧查看。")
        st.line_chart({"相对时间（s）": result["time_s"],
                       "车体（mm）": [row[0]*1000 for row in result["q"]],
                       "前构架（mm）": [row[2]*1000 for row in result["q"]],
                       "后构架（mm）": [row[4]*1000 for row in result["q"]]}, x="相对时间（s）")
        with st.expander("二系悬挂响应"):
            st.line_chart({"相对时间（s）": result["time_s"],
                           "前转向架二系行程（mm）": [row[0]*1000 for row in result["secondary_m"]],
                           "后转向架二系行程（mm）": [row[1]*1000 for row in result["secondary_m"]]}, x="相对时间（s）")
        with st.expander("工况对照"):
            st.button("将已计算工况设为对照基准", on_click=lambda: state.update(vehicle_reference=active))
            reference = state.get("vehicle_reference")
            if reference is not None:
                st.dataframe([{"工况": "保存的基准", **summary(_run(reference))},
                              {"工况": "当前计算", **summary(result)}], hide_index=True)
        with st.expander("模态与计算证据"):
            modes = result["modes"]
            st.dataframe({"固有频率（Hz）": modes["undamped_hz"],
                          "投影阻尼比": modes["modal_projected_damping_ratio"]}, hide_index=True)
            st.caption(result["evidence_id"])
            st.download_button("导出计算证据 JSON", json.dumps(result, ensure_ascii=False, allow_nan=False),
                               file_name="vehicle-response.json", mime="application/json", on_click="ignore")
        with st.expander("悬挂参数敏感性"):
            st.caption("逐项变化 ±5%。表中斜率按参数相对变化量归一，固定同一速度与轨面。")
            if st.button("计算局部敏感性"):
                st.dataframe(local_sensitivity(active), hide_index=True)
        _reference_validation(result)


def _reference_validation(result):
    with st.expander("用参考数据验证整车计算"):
        st.caption("参考数据须提前校准至 +Z、统一为 m/s²，并在同一测点与时间零点上配对。")
        st.download_button("下载配对 CSV 表头", ",".join(COLUMNS)+"\n", "vehicle-reference.csv",
                           "text/csv", on_click="ignore")
        channels = {"body_z": "车体中心", "front_bogie_z": "前构架中心", "rear_bogie_z": "后构架中心"}
        sources = {"synthetic": "虚构基准", "public_declared": "有授权的公开数据", "field_declared": "用户声明试验数据"}
        with st.form(f"vehicle_validation_{result['evidence_id']}"):
            channel = st.selectbox("参考测点", channels, format_func=channels.get)
            source = st.selectbox("参考来源", sources, format_func=sources.get)
            record = st.text_input("参考来源及授权记录", max_chars=200)
            alignment = st.text_input("方向、标定及时间配对依据", max_chars=200)
            file = st.file_uploader("参考配对 CSV", type=["csv"], max_upload_size=1)
            submitted = st.form_submit_button("核对整车残差")
        if not submitted:
            return
        if file is None:
            st.info("请上传参考配对 CSV。")
            return
        try:
            report = compare_vehicle_reference(result, channel, file.getvalue(), reference_kind=source,
                                               reference_record=record, alignment_record=alignment)
        except ValueError as exc:
            st.warning(str(exc))
            return
        st.dataframe([report["metrics"]], hide_index=True)
        st.download_button("导出整车验证报告", json.dumps(report, ensure_ascii=False, allow_nan=False),
                           "vehicle-validation.json", "application/json", on_click="ignore")
