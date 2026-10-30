"""Exploratory vibration dashboard, explicitly outside certified comfort grading."""

import sqlite3

import plotly.graph_objects as go
import streamlit as st
from rail_vehicle.ui import plot_chart, record_analysis
from rail_vehicle.deployment import demo_source_options

from rail_vehicle.demo_data import vibration_demo_samples
from rail_vehicle.replay import prepare_replay
from rail_vehicle.run_data import list_batch_samples, list_vehicle_batches
from rail_vehicle.vehicle_data import list_vehicles
from rail_vehicle.pages.experiment_export import render_export
from rail_vehicle.pages.experiment_ai import render_experiment_ai
from rail_vehicle.vibration import (
    STATE_KEY, VibrationInputError, VibrationUnavailableError, analyze_vibration,
    signal_libraries, vibration_fingerprint,
)


LABELS = dict(lateral_accel="横向", vertical_accel="垂向")
COLORS = dict(lateral_accel="#2563eb", vertical_accel="#7c3aed")


def vibration_chart(result, *, rolling=False):
    figure = go.Figure()
    for field, label in LABELS.items():
        channel = result["channels"][field]
        x = channel["rolling_end_seconds"] if rolling else channel["frequencies"]
        y = channel["rolling_rms"] if rolling else channel["psd"]
        figure.add_trace(go.Scatter(x=x, y=y, name=label, line_color=COLORS[field]))
    figure.update_layout(height=320, margin=dict(l=20, r=20, t=20, b=20),
                         legend=dict(orientation="h", y=1.12),
                         xaxis_title="窗口结束时刻（相对秒）" if rolling else "频率（Hz）",
                         yaxis_title="去均值滑动 RMS（m/s²）" if rolling else "PSD（(m/s²)²/Hz）")
    return figure


def render():
    st.header("振动与平稳性分析")
    st.caption("智能分析工作台 / 数据与参数 → 计算结果 → 证据与报告")
    st.info("探索性振动分析 · 提供未加权指标，不输出标准舒适等级、故障诊断或运行安全结论。")
    try:
        signal_libraries()
    except VibrationUnavailableError:
        st.session_state.pop(STATE_KEY, None)
        st.warning("振动分析可选依赖尚未安装，其他功能仍可使用。")
        st.code('.venv\\Scripts\\python.exe -m pip install -e ".[signal]"', language="powershell")
        return
    source = st.radio("振动数据来源", demo_source_options("历史运行批次", "内置高频虚构信号"), horizontal=True, key="vibration_source")
    try:
        if source == "内置高频虚构信号":
            samples = vibration_demo_samples()
            selection = dict(source="demo")
            st.caption("100 Hz、30 秒虚构数据。横向主成分 1.5 Hz，垂向主成分 6 Hz；18–20 秒设置幅值上升，不写入数据库。")
        else:
            vehicles = list_vehicles()
            if not vehicles:
                st.session_state.pop(STATE_KEY, None)
                st.info("请先登记车辆并导入数据，或选择内置高频虚构信号。")
                return
            vehicle = st.selectbox("振动分析车辆", [v["id"] for v in vehicles],
                                   format_func=lambda vid: next(v["vehicle_code"] for v in vehicles if v["id"] == vid), key="vibration_vehicle")
            batches = list_vehicle_batches(vehicle)
            if not batches:
                st.session_state.pop(STATE_KEY, None)
                st.info("该车辆尚无运行批次，请先导入数据。")
                return
            labels = {b["id"]: f"批次 {b['id']} · {b['file_name']} · {b['sample_count']} 条" for b in batches}
            batch = st.selectbox("振动分析批次", list(labels), format_func=labels.get, key=f"vibration_batch_{vehicle}")
            samples = list_batch_samples(vehicle, batch)
            selection = dict(source="history", vehicle=vehicle, batch=batch)
        # Validation also establishes UTC chronology before relative range selection.
        track = prepare_replay(samples)
        if track.duration > 0:
            lower, upper = st.slider("分析相对时间范围（秒）", 0.0, float(track.duration),
                                     (0.0, float(track.duration)), step=0.01,
                                     key=f"vibration_range_{selection}")
            selected = [row for row, offset in zip(track.samples, track.offsets) if lower <= offset <= upper]
        else:
            selected = track.samples
        columns = st.columns(3)
        band_low = columns[0].number_input("频带下限（Hz）", min_value=0.01, value=0.5, step=0.1, key="vibration_band_low")
        band_high = columns[1].number_input("频带上限（Hz）", min_value=0.02, value=20.0, step=1.0, key="vibration_band_high")
        window_seconds = columns[2].number_input("滑动 RMS 窗口（秒）", min_value=0.01, value=1.0, step=0.1, key="vibration_window")
        fingerprint = vibration_fingerprint(selected, selection, band_low=band_low,
                                             band_high=band_high, window_seconds=window_seconds)
    except (ValueError, TypeError, KeyError, OverflowError):
        st.session_state.pop(STATE_KEY, None)
        st.error("当前批次没有有效的运行数据，或参数无效，无法分析。")
        return
    except (sqlite3.Error, OSError):
        st.session_state.pop(STATE_KEY, None)
        st.error("暂时无法读取运行数据，请稍后重试。")
        return
    saved = st.session_state.get(STATE_KEY)
    if saved and saved["fingerprint"] != fingerprint:
        st.session_state.pop(STATE_KEY, None)
        saved = None
    if st.button("计算振动指标", type="primary", key="vibration_calculate"):
        st.session_state.pop(STATE_KEY, None)
        try:
            result = analyze_vibration(selected, band_low=band_low, band_high=band_high, window_seconds=window_seconds)
            saved = dict(fingerprint=fingerprint, result=result)
            st.session_state[STATE_KEY] = saved
        except (VibrationInputError, VibrationUnavailableError) as exc:
            st.warning(str(exc))
            return
        except Exception:
            st.error("振动计算失败，未展示结果；其他分析与报告功能仍可使用。")
            return
    if not saved:
        st.session_state.pop("experiment_vibration_ai", None)
        st.info("点击“计算振动指标”查看采样质量、时域与适用的频域结果。")
        return
    result = saved["result"]
    record_analysis("振动与平稳性分析", fingerprint, result["sample_count"], fictional=selection.get("source") == "demo")
    columns = st.columns(4)
    columns[0].metric("分析样本数", result["sample_count"], border=True)
    columns[1].metric("记录时长（秒）", f"{result['duration_seconds']:.2f}", border=True)
    rate = result["sampling_rate_hz"]
    columns[2].metric("估算采样率（Hz）", f"{rate:.2f}" if rate else "不可用", border=True)
    columns[3].metric("频域可用性", "可计算" if result["spectral_available"] else "条件不足", border=True)
    deviation = result["interval_deviation"]
    deviation_text = f"{deviation:.3%}" if deviation is not None else "不可计算"
    st.caption(f"重复 UTC 时间戳：{result['sampling']['duplicate_timestamp_rows']} 条；采样间隔最大相对偏差：{deviation_text}。频域门槛采用不超过 1% 的间隔偏差，不自动重采样。")
    st.subheader("未加权时域指标")
    rows = []
    for field, label in LABELS.items():
        channel = result["channels"][field]
        rows.append(dict(方向=label, 均值_ms2=channel["raw_mean"], 原始RMS_ms2=channel["raw_rms"],
                         去均值RMS_ms2=channel["dynamic_rms"], 去均值绝对峰值_ms2=channel["dynamic_peak"],
                         峰值因子=channel["crest_factor"]))
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("原始 RMS=√mean(a²)；去均值 RMS=√mean((a−mean(a))²)；峰值因子=去均值绝对峰值/去均值 RMS，零振动时不定义。均值去除不等同于标准频率加权或姿态/重力校正。")
    for issue in result["issues"]:
        st.warning(issue)
    if result["spectral_available"]:
        peaks = st.columns(2)
        for column, (field, label) in zip(peaks, LABELS.items()):
            peak = result["channels"][field]["dominant_frequency_hz"]
            column.metric(f"{label}频带内主峰（Hz）", f"{peak:.3f}" if peak is not None else "无非零振动")
        st.subheader("加速度功率谱（Welch）")
        plot_chart(vibration_chart(result), width="stretch", key="vibration_psd")
        st.caption(f"周期 Hann 窗、每段 {result['segment_samples']} 点、50% 重叠、每段去均值；共 {result['segment_count']} 段，频率间隔 {result['frequency_resolution_hz']:.4f} Hz。窗口按最低频率至少 5 个周期选择，不补零。主峰仅为当前频带内 PSD 最大频点，不用于推断故障部件。")
        st.dataframe([dict(方向=label, 频带_Hz=f"{band_low:g}–{band_high:g}",
                           未加权频带RMS_ms2=result["channels"][field]["band_rms"]) for field, label in LABELS.items()],
                      hide_index=True, width="stretch")
        st.caption("频带 RMS=√(Σ频带内 PSD×频率间隔)，使用闭区间内的离散频点，存在频率分辨率与谱泄漏误差；不代表加权舒适性指标。")
        st.subheader("滑动振动强度")
        if result["channels"]["lateral_accel"]["rolling_rms"]:
            plot_chart(vibration_chart(result, rolling=True), width="stretch", key="vibration_rolling")
            st.caption("每个滑动窗口对整段去均值后的加速度计算 RMS；窗口点数按采样率四舍五入，横轴表示窗口结束时刻。")
        else:
            st.info("当前滑动窗口不足 2 个样本或长于记录，未生成滑动曲线。")
    st.caption("本功能未实施标准频率加权或舒适等级判定。传感器安装位置、轴向、重力、抗混叠与测量链路均需核实；仅凭 CSV 不能确认这些条件。振动计算不调用 AI；可单独导出实验 PDF 或主动请求聚合指标解读。")
    ai_context, ai_result = render_experiment_ai(saved, fingerprint, selection, "vibration")
    render_export(saved, fingerprint, selection, "vibration", ai_context=ai_context, ai_result=ai_result)
