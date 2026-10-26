"""Cross-vehicle descriptive comparison using existing historical data."""

import sqlite3

import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.demo_data import ml_demo_samples
from rail_vehicle.fleet_comparison import compare_vehicles
from rail_vehicle.run_data import list_batch_samples, list_vehicle_batches
from rail_vehicle.vehicle_data import list_vehicles


def demo_entries():
    reference, target = ml_demo_samples()
    return [dict(vehicle=dict(id=index, vehicle_code=f"虚构演示-{index}",
                              vehicle_type="演示车型", mass_kg=42000,
                              axle_count=4, bogie_count=2), batch_id=index, samples=samples)
            for index, samples in enumerate((reference, target), start=1)]


def comparison_chart(results, field, unit):
    figure = go.Figure()
    colors = ("#2563eb", "#ea580c", "#7c3aed", "#059669", "#db2777", "#475569")
    for result, color in zip(results, colors):
        # Markers avoid suggesting interpolation across gaps introduced by filtering.
        figure.add_trace(go.Scatter(x=[s["relative_time_seconds"] for s in result["series"]],
                                    y=[s[field] for s in result["series"]],
                                    mode="markers", marker=dict(size=4, color=color), name=result["label"]))
    figure.update_layout(height=300, xaxis_title="相对首个保留样本的运行时间（秒）",
                         yaxis_title=unit, margin=dict(l=20, r=20, t=20, b=20))
    return figure


def render():
    st.header("多车辆运行对比")
    st.info("描述性运行表现对比，不代表同工况试验，不输出故障、安全或车辆优劣结论。")
    source = st.radio("对比数据来源", ["历史车辆与批次", "内置虚构对比"], horizontal=True, key="fleet_source")
    try:
        if source == "内置虚构对比":
            entries = demo_entries()
            st.caption("两辆虚构车辆使用独立的 400/160 条信号，包含不同速度与振动幅值；仅用于展示，不写入数据库。")
        else:
            vehicles = list_vehicles()
            if len(vehicles) < 2:
                st.info("请先登记至少两辆车并导入各自运行数据，或选择内置虚构对比。")
                return
            by_id = {v["id"]: v for v in vehicles}
            selected = st.multiselect("选择 2–6 辆车辆", list(by_id),
                                      format_func=lambda vid: by_id[vid]["vehicle_code"], max_selections=6)
            if len(selected) < 2:
                st.info("请选择至少两辆不同车辆。")
                return
            entries = []
            for vid in selected:
                batches = list_vehicle_batches(vid)
                if not batches:
                    st.warning("所选车辆中有车辆尚未导入运行数据。")
                    return
                labels = {b["id"]: f"批次 {b['id']} · {b['sample_count']} 条" for b in batches}
                bid = st.selectbox(f"{by_id[vid]['vehicle_code']} 的运行批次", list(labels),
                                   format_func=labels.get, key=f"fleet_batch_{vid}")
                entries.append(dict(vehicle=by_id[vid], batch_id=bid, samples=list_batch_samples(vid, bid)))
        low, high = st.slider("共同速度区间（km/h，含边界）", 0, 600, (0, 600))
        if not st.button("计算多车辆对比", type="primary"):
            st.caption("选择数据和区间后点击计算；更改选择后需重新计算，不保留旧结果。")
            return
        comparison = compare_vehicles(entries, low, high)
    except (sqlite3.Error, OSError):
        st.error("暂时无法读取车辆运行数据，请稍后重试。")
        return
    except (ValueError, TypeError, KeyError, OverflowError):
        st.warning("无法对比：请检查各批次数据和速度区间，确保每辆车都有有效样本。")
        return
    for warning in comparison["warnings"]:
        st.warning(warning)
    results = comparison["results"]
    st.subheader("车辆与数据范围")
    st.dataframe([dict(车辆=r["label"], 批次=r["batch_id"], 车型=r["vehicle"]["vehicle_type"],
                       登记质量_kg=r["vehicle"]["mass_kg"], 轴数=r["vehicle"]["axle_count"],
                       转向架数量=r["vehicle"]["bogie_count"], 原始样本=r["source_count"],
                       保留样本=r["sample_count"], 保留比例=r["retained_fraction"],
                       首末保留样本跨度_s=r["duration_seconds"],
                       间隔中位数_s=r["sampling_summary"]["interval_median_seconds"],
                       最大间隔_s=r["sampling_summary"]["interval_max_seconds"],
                       重复时间戳=r["sampling_summary"]["duplicate_timestamp_rows"]) for r in results],
                 hide_index=True, width="stretch")
    st.subheader("基础动力学统计对比")
    st.dataframe([dict(车辆=r["label"], 平均速度_kmh=r["mean_speed_kmh"], 最大速度_kmh=r["maximum_speed_kmh"],
                       横向RMS_ms2=r["lateral_accel_rms"], 垂向RMS_ms2=r["vertical_accel_rms"],
                       横向绝对峰值_ms2=r["lateral_accel_peak_absolute"],
                       垂向绝对峰值_ms2=r["vertical_accel_peak_absolute"]) for r in results],
                 hide_index=True, width="stretch")
    st.caption("按筛选后样本等权计算：均值=Σx/N，RMS=√(Σa²/N)，绝对峰值=max|a|。不按时间加权，不进行质量归一化或自动排名。")
    for field, title, unit in (("speed_kmh", "速度对比", "速度（km/h）"),
                               ("lateral_accel", "横向加速度对比", "横向加速度（m/s²）"),
                               ("vertical_accel", "垂向加速度对比", "垂向加速度（m/s²）")):
        st.subheader(title)
        st.plotly_chart(comparison_chart(results, field, unit), width="stretch")
    st.caption("各车从首个保留样本对齐为 0 秒；保留真实时间间隔。散点不插值，不代表同一线路位置或同步测量。筛选后的跨度包含未保留样本形成的空档，并非累计运行时长。传感器安装、轴向、载荷和线路条件需人工核实。")
