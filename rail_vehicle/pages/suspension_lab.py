"""Parameter controls for the independent vertical response calculation."""
from dataclasses import replace
import json

import streamlit as st

from rail_vehicle.bogie import PARTS
from rail_vehicle.bogie_viewer import render_webgl, webgl_available
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.pages.sensors import read_snapshot
from rail_vehicle.replay import ReplayState
from rail_vehicle.sensor_evidence import project_frame_response
from rail_vehicle.suspension import SCENARIOS, SuspensionCase, simulate
from rail_vehicle.two_stage_suspension import TwoStageCase, simulate_two_stage
from rail_vehicle.verification_residuals import COLUMNS, compare_projection, projection_digest


@st.cache_data(max_entries=16, show_spinner=False)
def _calculate(case):
    if isinstance(case, TwoStageCase):
        return simulate_two_stage(case)
    result = simulate(case)
    if case.scenario == "soft":
        result["nominal_travel_m"] = simulate(replace(case, scenario="rough"))["channels"]["travel_m"]
    return result


def render_lab(main):
    labels = {**SCENARIOS, "two_stage": "一系与二系耦合浮沉"}
    scenario = st.selectbox("计算工况", list(labels), index=1, format_func=labels.get, key="sim_case")
    two_stage = scenario == "two_stage"
    speed = st.slider("初速（m/s）", 1, 10, 6)
    with st.expander("悬挂参数"):
        mass = st.slider("等效质量（kg）", 500, 2500, 1200, 50)
        stiffness = st.slider("名义刚度（kN/m）", 100, 1000, 450, 25)
        damping = st.slider("阻尼（kN·s/m）", 1, 40, 10)
        amplitude = st.slider("激励幅值（mm）", 0, 8, 4,
                              disabled=scenario not in ("rough", "flat", "soft", "axle_bump", "two_stage"))
        primary = SuspensionCase("rough" if two_stage else scenario, mass, stiffness*1000, damping*1000, speed, amplitude/1000)
        if two_stage:
            body = st.slider("车体等效质量（kg）", 2000, 18000, 6000, 100)
            secondary_k = st.slider("二系刚度（kN/m）", 60, 600, 240, 5)
            secondary_c = st.slider("二系阻尼（kN·s/m）", 1, 40, 12)
            comparison_k = st.slider("对照工况二系刚度（kN/m）", 60, 600, 290, 5)
            case = TwoStageCase(primary, body, secondary_k*1000, secondary_c*1000)
        else:
            case = primary
    result = _calculate(case)
    with main:
        st.subheader("悬挂响应试验")
        dual = scenario == "axle_bump"
        if dual:
            st.caption(f"构架浮沉与点头 · B 轴在前，A 轴在后 · 轴距 {result['wheelbase_m']:.2f} m"
                       f" · 同一凸起到达两轴的时间差 {result['axle_delay_s']:.3f} s")
        elif two_stage:
            st.caption("二自由度垂向模型 · 轮座周期输入 · 构架和车体以二系悬挂耦合")
        else:
            st.caption(f"单自由度垂向模型 · 有效刚度 {case.stiffness/1000:g} kN/m · 无阻尼固有频率 {result['natural_hz']:.2f} Hz")
        notes = {
            "rough": "支承输入为波长 3 m 的正弦波。",
            "soft": "名义刚度降低 50%，与周期不平顺工况采用相同输入。",
            "flat": "每转施加一次平滑下凹的等效支承输入，仅代表周期激励。",
            "brake": "设定四秒匀减速至停车，按行程计算轮轴转角。",
            "curve": "在局部切线视角展示预设的过渡横移，轨道几何保持平直。",
            "hunting": "横移与摇头使用预设相位关系。",
            "level": "平直轨道、零垂向输入，轮轴按设定初速滚动。",
            "axle_bump": "沿 +X 匀速通过 1 m 长的余弦凸起。三维视窗以两轮座连线为参考，显示两端悬挂行程。读数中的构架点头角以水平面为参考。",
            "two_stage": "两轴轮座采用相同的 3 m 波长输入。三维显示构架与轮座的计算相对运动，车体与二系行程见曲线。",
        }
        if webgl_available():
            render_webgl(mode="正常运行", phase=0, state=ReplayState(), visible=PARTS,
                         highlight=(), view="整体视图", focus=None, labels=False, rails=True,
                         exploded=False, spread=0, camera_revision=0, continuous=False, height=840,
                         key="suspension_model", simulation=result)
        else:
            st.info("三维资源不完整，可展开下方数值样本查看计算结果。")
        motion_note = ("参数为教学假设，车轮按给定速度滚动。" if dual else
                       "模型以轮座为参考，构架按计算行程移动，一系弹簧与减振器跟随端点。"
                       "参数为教学假设，横移、摇头和轮转为运动学示意。")
        st.caption(notes[scenario] + motion_note)
        if two_stage:
            comparison = _calculate(replace(case, secondary_stiffness_n_m=comparison_k*1000))
            _show_two_stage(case, result, comparison)
        target = st.session_state.get("frame_sensor_target")
        if target and not two_stage and not public_demo_enabled():
            _show_projection(target, result)
        with st.expander("计算方法与数值样本"):
            if dual:
                st.latex(r"m\ddot z=F_B+F_A,\quad I_y\ddot\theta=\tfrac{L}{2}(F_B-F_A)")
                st.caption(f"构架为对称刚体，前后两端各取一半总刚度和阻尼。转动惯量按假设回转半径 1 m 计算，为 "
                           f"{result['pitch_inertia_kg_m2']:g} kg·m²。正点头角表示 B 端抬升。"
                           f"浮沉、点头无阻尼固有频率分别为 {result['natural_hz']:.2f} Hz、"
                           f"{result['pitch_natural_hz']:.2f} Hz。")
            elif two_stage:
                st.latex(r"m_f\ddot z_f=F_1+F_2,\quad m_b\ddot z_b=-F_2")
                st.latex(r"F_1=k_1(y-z_f)+c_1(\dot y-\dot z_f),\quad F_2=k_2(z_b-z_f)+c_2(\dot z_b-\dot z_f)")
            else:
                st.latex(r"m\ddot z+c(\dot z-\dot y)+k(z-y)=0")
            st.caption("初始位移和速度均为零。四阶 Runge–Kutta 积分步长 1 ms，每 10 ms 输出一次。")
            c = result["channels"]
            table = {"时间（s）": [i/result["sample_hz"] for i in range(len(c["frame_m"]))],
                     "构架位移（m）": c["frame_m"], "构架加速度（m/s²）": c["accel_m_s2"]}
            if dual:
                table.update({"B 轴支承（m）": c["front_support_m"], "A 轴支承（m）": c["rear_support_m"],
                              "B 端行程（m）": c["front_travel_m"], "A 端行程（m）": c["rear_travel_m"],
                              "构架点头角（rad）": c["pitch_rad"]})
            else:
                table.update({"支承（m）": c["support_m"], "悬挂行程（m）": c["travel_m"]})
            st.dataframe(table, hide_index=True)


def _show_two_stage(case, result, comparison):
    channels = result["channels"]
    st.subheader("一系与二系计算行程")
    time_column = "相对时间（s）"
    st.line_chart({time_column: [i/result["sample_hz"] for i in range(len(channels["travel_m"]))],
                   "一系行程（mm）": [value*1000 for value in channels["travel_m"]],
                   "二系行程（mm）": [value*1000 for value in channels["secondary_travel_m"]],
                   "对照工况二系行程（mm）": [value*1000 for value in comparison["channels"]["secondary_travel_m"]]},
                  x=time_column, x_label="相对时间（s）", y_label="计算行程（mm）")
    with st.expander("固有模态与频率响应"):
        st.dataframe(result["modes"], column_config={"natural_hz": "固有频率（Hz）",
                                                     "modal_damping_ratio": "模态投影阻尼比"}, hide_index=True)
        st.caption("频率按无阻尼模型计算。阻尼比为无阻尼模态上的投影值，仍可能存在模态之间的阻尼耦合。")
        rows = [(i/10, *map(abs, case.response(i/10))) for i in range(151)]
        st.line_chart({"频率（Hz）": [row[0] for row in rows], "构架": [row[1] for row in rows],
                       "车体": [row[2] for row in rows]}, x="频率（Hz）", y_label="单位支承输入位移增益")


def _show_projection(target, result):
    vehicle, batch, channel, version = target
    snapshot = read_snapshot(vehicle, batch)
    try:
        projection = project_frame_response(snapshot, channel, version, result)
    except ValueError as exc:
        st.info(str(exc))
        return
    binding = projection["configuration"]
    st.subheader("沿登记测量轴的计算响应")
    st.caption(f"{snapshot['vehicle_code']} · 批次 #{batch} · {binding['serial']} · {channel} · v{version}。"
               "横轴为仿真相对时间，曲线为构架垂向计算加速度在登记轴上的分量。")
    values = projection["axis_accel_m_s2"]
    st.line_chart({"仿真时间（s）": [i/projection["sample_hz"] for i in range(len(values))],
                   "计算轴向加速度（m/s²）": values}, x="仿真时间（s）", y="计算轴向加速度（m/s²）")
    st.download_button("下载投影计算依据", json.dumps(projection, ensure_ascii=False),
                       file_name=f"simulation-batch-{batch}-{channel}-v{version}.json", mime="application/json")
    with st.expander("参考轴向数据与仿真残差"):
        scope = projection_digest(projection)
        st.caption("参考值须采用同一测量轴，单位为 m/s²，并配对至仿真输出时刻。请记录参考来源和相对时间零点依据。")
        st.download_button("下载参考 CSV 表头", ",".join(COLUMNS) + "\n",
                           file_name="simulation-reference-template.csv", mime="text/csv", on_click="ignore")
        kinds = {"synthetic": "虚构参考信号", "field_declared": "现场来源（用户声明）"}
        with st.form(f"residual_{scope}", border=False):
            kind = st.selectbox("参考来源类别", kinds, index=None, format_func=kinds.get)
            reference = st.text_input("参考记录编号或说明", max_chars=200)
            alignment = st.text_input("相对时间零点与配对依据", max_chars=200)
            file = st.file_uploader("已配对的参考 CSV", type=["csv"], max_upload_size=1)
            submitted = st.form_submit_button("计算残差")
        if not submitted:
            return
        if file is None:
            st.info("请上传已经配对的参考 CSV。")
            return
        try:
            report = compare_projection(projection, file.getvalue(), reference_kind=kind,
                                        reference_record=reference, alignment_record=alignment)
        except ValueError as exc:
            st.info(str(exc))
            return
        metrics = report["metrics"]
        st.caption(f"{metrics['pairs']} 个配对点 · 独立实测仍待验证")
        for col, (label, field) in zip(st.columns(4), (("RMSE", "rmse_m_s2"), ("MAE", "mae_m_s2"),
                                                      ("绝对峰值误差", "peak_abs_error_m_s2"), ("Pearson r", "pearson_r")),
                                       strict=True):
            value = metrics[field]
            units = "" if field == "pearson_r" else " m/s²"
            col.metric(label, "未定义" if value is None else f"{value:.4g}{units}")
        st.download_button("导出验证记录 JSON", json.dumps(report, ensure_ascii=False),
                           file_name=f"validation-batch-{batch}-{channel}-v{version}.json", mime="application/json",
                           on_click="ignore")
