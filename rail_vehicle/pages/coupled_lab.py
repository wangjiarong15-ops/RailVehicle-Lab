"""Whole-vehicle simulation, bounded comparison and archival."""

from dataclasses import replace
import csv
from io import StringIO
import sqlite3

import streamlit as st

from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.coupled_vehicle import (CoupledCase, TRACKS, case_config, frequency_response,
                                          playback, read_case, simulate, summary)
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.replay import ReplayState
from rail_vehicle.ui import COPILOT, navigate
from rail_vehicle.virtual_trials import (TrialPlan, json_text, load_trial, rerun_trial, run_trial,
                                          save_trial, trial_history, trial_pdf)


@st.cache_data(max_entries=12, show_spinner=False)
def _calculate(case, substeps):
    return simulate(case, substeps=substeps)


def _set_case(case):
    state = st.session_state
    state["coupled_case"] = case
    state["coupled_substeps"] = 5
    state["coupled_revision"] = state.get("coupled_revision", 0)+1
    state.pop("coupled_trial", None)


def _parameters(case):
    state = st.session_state
    version = state.get("coupled_revision", 0)
    v, p = case.vehicle, case.transverse
    with st.form(f"coupled_parameters_{version}", border=False):
        st.subheader("整车工况")
        kinds = list(TRACKS)
        if not case.profile:
            kinds.remove("custom")
        track = st.selectbox("轨道激励", kinds, index=kinds.index(case.track), format_func=TRACKS.get)
        speed = st.number_input("运行速度（m/s）", 2., 70., float(case.speed_m_s), step=1.)
        length = st.number_input("空间尺度（m）", 2., 40., float(case.length_m), step=.5)
        lateral = st.number_input("横向输入幅值（mm）", 0., 20., case.lateral_m*1000, step=.5)
        vertical = st.number_input("垂向输入幅值（mm）", 0., 12., case.vertical_m*1000, step=.5)
        duration = st.number_input("仿真时长（s）", 1, 12, case.duration_s)
        stiffness = st.number_input("二系横向刚度（kN/m）", 30., 900., p.secondary_y_k_n_m/1000, step=5.)
        damping = st.number_input("二系横向阻尼（kN·s/m）", .5, 100., p.secondary_y_c_ns_m/1000, step=1.)
        with st.expander("车辆与垂向悬挂参数"):
            mass = st.number_input("车体质量（kg）", 10000., 65000., float(v.body_mass_kg), step=500.)
            bogie_mass = st.number_input("转向架质量（kg）", 1000., 6000., float(v.bogie_mass_kg), step=100.)
            center = st.number_input("转向架中心半距（m）", 3., 12., float(v.bogie_half_spacing_m), step=.1)
            first_k = st.number_input("一系垂向刚度（kN/m）", 200., 3000., v.primary_k_n_m/1000, step=50.)
            first_c = st.number_input("一系垂向阻尼（kN·s/m）", 1., 80., v.primary_c_ns_m/1000, step=1.)
            second_k = st.number_input("二系垂向刚度（kN/m）", 100., 3000., v.secondary_k_n_m/1000, step=50.)
            second_c = st.number_input("二系垂向阻尼（kN·s/m）", 1., 150., v.secondary_c_ns_m/1000, step=1.)
            antiroll = st.number_input("抗侧滚刚度（kN·m/rad）", 0., 1000., p.antiroll_k_nm_rad/1000, step=10.)
            seed = st.number_input("随机种子", 0, 2**32-1, case.seed)
        submitted = st.form_submit_button("计算整车响应", type="primary", width="stretch")
    if submitted:
        try:
            updated = replace(case, track=track, speed_m_s=speed, length_m=length,
                              profile=case.profile if track == "custom" else (), lateral_m=lateral/1000,
                              vertical_m=vertical/1000, duration_s=duration, seed=seed,
                              vehicle=replace(v, body_mass_kg=mass, bogie_mass_kg=bogie_mass,
                                              bogie_half_spacing_m=center, primary_k_n_m=first_k*1000,
                                              primary_c_ns_m=first_c*1000, secondary_k_n_m=second_k*1000,
                                              secondary_c_ns_m=second_c*1000),
                              transverse=replace(p, secondary_y_k_n_m=stiffness*1000,
                                                 secondary_y_c_ns_m=damping*1000, antiroll_k_nm_rad=antiroll*1000))
        except ValueError as exc:
            st.error(str(exc))
        else:
            _set_case(updated)
            st.rerun()
    with st.expander("保存或载入工况"):
        st.download_button("下载工况 JSON", json_text(case_config(case)), "coupled-vehicle.json",
                           "application/json", on_click="ignore")
        sample = replace(case, track="custom", profile=((0, 0, 0), (3, .6, 1), (8, 0, 0)))
        st.download_button("下载自定义激励示例", json_text(case_config(sample)), "coupled-custom-template.json",
                           "application/json", on_click="ignore")
        uploaded = st.file_uploader("工况 JSON", type=["json"], key="coupled_upload", max_upload_size=1)
        if uploaded is not None and st.button("载入工况"):
            try:
                _set_case(read_case(uploaded.getvalue()))
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()


def _response_csv(result):
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(("t_s", "evidence_id", "source", *(
        f"{dof}_{suffix}_{unit}{time_unit}" for suffix, time_unit in (("q", ""), ("dq", "_per_s"), ("ddq", "_per_s2"))
        for dof, unit in zip(result["dofs"], result["units_q"], strict=True))))
    for t, q, dq, ddq in zip(result["time_s"], result["q"], result["dq"], result["ddq"], strict=True):
        writer.writerow((t, result["evidence_id"], "teaching_simulation", *q, *dq, *ddq))
    return buffer.getvalue()


def _viewer(result, key, bogie, gain):
    if webgl_available():
        render_webgl(mode="正常运行", phase=0, state=ReplayState(), visible=PARTS,
                     highlight=(), view="整体视图", focus=None, labels=False, rails=True,
                     exploded=False, spread=0, camera_revision=0, continuous=False, height=620,
                     key=key, simulation=playback(result, bogie, gain))
    else:
        st.info("本地三维资源不可用。可下载下方数值结果。")


def _trial_table(report):
    reasons = {"roll": "侧滚", "travel": "横向行程", "contact": "接触假设",
               "no_lateral_input": "无横向激励", "incomplete_excitation": "尾轴激励不完整"}
    return [{"编号": i, "速度 m/s": row["speed_m_s"], "刚度倍数": row["stiffness_factor"],
             "阻尼倍数": row["damping_factor"], "横向 RMS m/s²": row["metrics"]["body_y_rms_m_s2"],
             "侧滚峰值 mrad": row["metrics"]["roll_peak_mrad"], "二系横向行程 mm": row["metrics"]["secondary_y_peak_mm"],
             "约束": "、".join(reasons[r] for r in row["violations"]) or "通过"}
            for i, row in enumerate(report["candidates"], 1)]


def _trials(case):
    st.subheader("参数扫描与复算")
    st.caption("每轴填 1 至 3 个值，总计最多 27 个工况。初筛通过的工况以 0.5 ms 步长复算，"
               "再按全时窗的车体横向加速度 RMS 排序。尾轴未完整受激的短试验不会参选。")
    with st.form(f"coupled_scan_{st.session_state.get('coupled_revision', 0)}", border=False):
        a,b,c = st.columns(3)
        speeds = a.text_input("速度（m/s，逗号分隔）", f"{case.speed_m_s:g}", max_chars=64)
        stiffness = b.text_input("二系横向刚度倍数", "0.7, 1, 1.3", max_chars=64)
        damping = c.text_input("二系横向阻尼倍数", "0.7, 1, 1.3", max_chars=64)
        a,b = st.columns(2)
        roll = a.number_input("侧滚峰值上限（mrad）", .01, 100., 5., step=.5)
        travel = b.number_input("二系横向行程上限（mm）", .01, 200., 20., step=1.)
        run = st.form_submit_button("运行参数扫描", type="primary")
    if run:
        st.session_state.pop("coupled_trial", None)
        try:
            axes = [tuple(float(value.strip()) for value in text.replace("，", ",").split(","))
                    for text in (speeds, stiffness, damping)]
            with st.spinner("正在计算和复算试验工况…"):
                st.session_state["coupled_trial"] = run_trial(case, TrialPlan(*axes, roll, travel))
        except (ValueError, OverflowError) as exc:
            st.error(str(exc))
    if not public_demo_enabled():
        with st.expander("历史试验"):
            try:
                items = trial_history()
                if not items:
                    st.caption("暂无已保存的试验。")
                else:
                    labels = {row["study_id"]: f"{row['created_at']} UTC · {row['study_id'][6:18]}" for row in items}
                    selected = st.selectbox("已归档试验", list(labels), format_func=labels.get)
                    if st.button("从配置复算历史试验"):
                        original = load_trial(selected)
                        if original is None:
                            st.warning("这条试验记录已不可用。")
                        else:
                            with st.spinner("正在从归档配置重新计算…"):
                                report, response = rerun_trial(original)
                            st.session_state["coupled_trial"] = (report, response)
                            if report["study_id"] == selected:
                                st.success("复算证据摘要与历史记录一致。")
                            else:
                                st.warning("复算已生成新的证据摘要，请对照历史报告复核。")
            except (sqlite3.Error, ValueError, KeyError):
                st.warning("历史试验暂时无法读取或复算。")
    if "coupled_trial" not in st.session_state:
        return
    report, response = st.session_state["coupled_trial"]
    st.caption(f"{len(report['candidates'])} 个工况 · 教学计算 · {report['study_id'][6:22]}")
    st.dataframe(_trial_table(report), hide_index=True)
    if report["sensitivity"]:
        with st.expander("查看参数灵敏度"):
            names = {"speed_m_s": "每 1 m/s", "stiffness_factor": "每 1 个刚度倍数", "damping_factor": "每 1 个阻尼倍数"}
            st.dataframe([{"对照尺度":names[row["axis"]], "匹配组数":row["matched_pairs"],
                           "平均 RMS 变化":row["mean"], "最小":row["min"], "最大":row["max"]}
                          for row in report["sensitivity"]], hide_index=True)
            st.caption("针对每一轴，固定其他参数后比较该组最小值和最大值。RMS 变化单位为 m/s²。")
    choice = report["recommendation"]
    if choice:
        base = report["refined_baseline"]["metrics"]["body_y_rms_m_s2"]
        after = choice["metrics"]["body_y_rms_m_s2"]
        text = f"网格最优可行工况 #{choice['index']+1} 已复算。基准与工况的横向 RMS 分别为 {base:.5g} 和 {after:.5g} m/s²。"
        if after < base:
            st.success(text)
        else:
            st.info(text + " 当前网格未找到低于基准的响应。")
        if response and st.toggle("查看复算方案三维与曲线"):
            _viewer(response, "coupled_trial_player", "front", 2)
    else:
        st.warning("当前网格没有复算通过的方案。可检查约束、输入范围或积分收敛。")
    a,b,c = st.columns(3)
    a.download_button("试验证据 JSON", json_text(report), "coupled-trial.json", "application/json", on_click="ignore")
    b.download_button("试验报告 PDF", trial_pdf(report), "coupled-trial.pdf", "application/pdf", on_click="ignore")
    if not public_demo_enabled() and c.button("保存到试验历史"):
        try:
            save_trial(report)
        except (sqlite3.Error, ValueError):
            st.error("试验记录暂时无法保存。")
        else:
            st.success("试验已保存，可通过历史记录复算。")


def render_coupled_lab(main):
    case = st.session_state.get("coupled_case", CoupledCase())
    substeps = st.session_state.get("coupled_substeps", 5)
    _parameters(case)
    with main:
        st.subheader("整车虚拟试验台")
        st.caption(f"19 自由度线性教学计算 · 积分步长 {5/substeps:g} ms · 尚无独立实车验证")
        with st.spinner("正在计算整车响应…"):
            result = _calculate(case, substeps)
        values = summary(result)
        for col, label, value in zip(st.columns(3), ("横向加速度 RMS", "垂向加速度 RMS", "车体侧滚峰值"),
                                     (f"{values['body_y_rms_m_s2']:.4g} m/s²", f"{values['body_z_rms_m_s2']:.4g} m/s²",
                                      f"{values['roll_peak_mrad']:.4g} mrad"), strict=True):
            col.metric(label, value)
        if values["contact_assumption_exceeded"]:
            st.warning("输出样本中线性接触载荷达到非正值，当前接触模型不适用于此工况。")
        if result["axle_delay_s"][-1] > case.duration_s:
            st.info("最后一组轮对在试验时长结束前尚未到达激励起点。")
        a,b,c = st.columns([1,1,1])
        bogie = a.radio("GLB 检查对象", ("front", "rear"), horizontal=True,
                       format_func={"front":"前转向架","rear":"后转向架"}.get)
        gain = b.selectbox("请求动画倍率", (1,2,4), index=1, format_func=lambda x:f"{x}×")
        c.button("将当前工况交给 Copilot", on_click=navigate, args=(COPILOT,), kwargs={"cp_coupled_case": case})
        _viewer(result, "coupled_lab_player", bogie, gain)
        st.caption("时间轴及数值使用计算样本。整车总览使用同一固定显示倍率，按全段运动幅值限幅。"
                   "局部转向架展示安装点连接，轮对横向由轨面输入规定，钢轨为几何参照。")
        with st.expander("频率响应、固有模态与数值导出"):
            st.latex(r"M\ddot q+C\dot q+Kq=F(t)")
            st.caption(f"Newmark 平均加速度法，积分步长 {5/substeps:g} ms，输出 200 Hz。"
                       "固有频率按无阻尼模型计算，阻尼比为对应模态的投影。")
            modes = result["modes"]
            ratio = values["y_displacement_rms_ratio"]
            if ratio is not None:
                st.caption(f"车体横向位移 RMS / 首轴轨面输入 RMS = {ratio:.4g}，"
                           f"横向加速度绝对峰值 {values['peak_y_m_s2']:.4g} m/s²，时刻 {values['peak_y_time_s']:.3f} s。")
            st.dataframe({"固有频率（Hz）":modes["undamped_hz"], "投影阻尼比":modes["modal_projected_damping_ratio"]},
                         hide_index=True)
            freq = frequency_response(case, [i/10 for i in range(151)])
            st.line_chart(freq, x="frequency_hz", y="body_y_over_input", x_label="频率（Hz）",
                          y_label="车体横向位移 / 轨面横向位移")
            st.download_button("导出计算响应 CSV", _response_csv(result), "coupled-response.csv",
                               "text/csv", on_click="ignore")
        _trials(case)
