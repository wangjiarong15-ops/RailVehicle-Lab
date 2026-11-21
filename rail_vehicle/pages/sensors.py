"""Batch-scoped controls shared by import, analysis and replay."""

import json
import sqlite3
from inspect import signature

import streamlit as st

from rail_vehicle.calibration_check import REFERENCE_COLUMNS, check_reference
from rail_vehicle.sensor_evidence import observation, scope_evidence, supports_frame_projection
from rail_vehicle.sensor_mapping import AXES, CHANNELS, PARTS, STATES, UNITS, registration
from rail_vehicle.sensor_store import STATE_NAMES, active_channels, channel_inventory, revise, revisions, sensor_snapshot

_LAZY_EXPANDER = "on_change" in signature(st.expander).parameters


def read_snapshot(vehicle, batch):
    try:
        return sensor_snapshot(vehicle, batch)
    except (sqlite3.Error, OSError):
        st.warning("暂时无法读取传感器档案。可继续按原始 CSV 通道分析。")
        return {}


def _edit(snapshot, channel, key):
    vid, bid = snapshot["vehicle_id"], snapshot["batch_id"]
    record = snapshot["channels"].get(channel, {})
    m, version = record.get("metadata") or {}, record.get("version", 0)
    opened = f"{key}_{channel}_opened_version"
    if st.session_state.setdefault(opened, version) != version:
        st.warning("此通道已有新版本。请重新载入后核对，再提交修改。")
        if st.button("载入最新配置", key=opened+"_reload"):
            st.session_state[opened] = version
            st.rerun()
        return
    prefix = f"{key}_{channel}_{version}"

    def choice(label, field, options, names=None):
        items = [None, *options]
        return st.selectbox(label, items, index=items.index(m.get(field)), key=prefix+field,
                            format_func=lambda v: ((names or {}).get(v, v) if v else "未提供"))

    with st.form(prefix):
        a, b = st.columns(2)
        with a:
            serial = st.text_input("传感器唯一编号", value=record.get("serial", ""), max_chars=64)
            part = choice("安装部件", "part_id", PARTS, {k: f"{p['label']} · {k}" for k, p in PARTS.items()})
            origin = choice("配置性质", "origin", ("recorded","illustration"),
                            {"recorded":"现场记录","illustration":"虚构教学示意"})
            frame = choice("安装位置坐标系", "position_frame", ("model","vehicle"), {"model":"模型","vehicle":"车辆"})
            mount = choice("安装点核验状态", "mount_status", STATES, STATE_NAMES)
        with b:
            source = st.text_input("来源凭据或记录编号", value=m.get("source",""), max_chars=200)
            axis = choice("传感器局部测量正方向", "axis", AXES)
            unit = choice("CSV 通道单位", "unit", UNITS)
            calibration = choice("轴向标定状态", "calibration", STATES, STATE_NAMES)
            basis = choice("记录时间基准", "time_basis", ("utc","device_offset"),
                           {"utc":"UTC / 显式时区","device_offset":"设备时间加固定偏移至 UTC"})
            clock = choice("时钟核验状态", "clock_status", STATES, STATE_NAMES)
        st.caption("位置用米。传感器、车辆、模型均为右手系。矩阵按行填写，左乘列向量。空白表示未知。")
        st.caption("模型原点在两轴之间的轨顶中心，+X 从 A 轴指向 B 轴，+Y 从模型左侧指向右侧，+Z 向上。"
                   "车辆原点取车体中心的轨面投影，+X 取登记的前向，+Z 向上。局部轴遵循传感器标识。")
        raw = {}
        for field, label in (("position_m","安装位置 [x,y,z]（m）"),
                             ("sensor_to_vehicle","传感器到车辆旋转矩阵（3×3）"),
                             ("vehicle_to_model","车辆到模型旋转矩阵（3×3）"),
                             ("vehicle_origin_model_m","车辆原点在模型中的位置 [x,y,z]（m）")):
            raw[field] = st.text_input(label, json.dumps(m[field]) if field in m else "", key=prefix+field)
        cols = st.columns(4)
        for col, (field, label) in zip(cols, (("rate_hz","采样率（Hz）"),("gain","增益"),("bias","偏置（通道单位）"),
                                               ("clock_offset_s","时钟偏移（s）")), strict=True):
            raw[field] = col.text_input(label, str(m.get(field,"")), key=prefix+field)
        save = st.form_submit_button("保存配置版本", type="primary")
    withdraw = record.get("metadata") is not None and st.button("撤销此通道绑定（保留版本）", key=prefix+"_remove")
    if not (save or withdraw):
        return
    try:
        metadata = None
        if save:
            metadata = dict(part_id=part, origin=origin, source=source.strip(), position_frame=frame,
                            mount_status=mount, axis=axis, unit=unit, calibration=calibration, time_basis=basis,
                            clock_status=clock)
            try:
                metadata.update({k: json.loads(v) for k, v in raw.items() if v.strip()})
            except (ValueError, RecursionError):
                raise ValueError("请使用数字、[x,y,z] 或三行数组，如 [[1,0,0],[0,1,0],[0,0,1]]。") from None
        revise(vid, bid, channel, serial.strip() if save else record["serial"], metadata, expected_version=version)
    except ValueError as exc:
        st.error(str(exc))
    except (sqlite3.Error, OSError):
        st.error("传感器配置未保存，请核对数据库状态。")
    else:
        st.session_state[opened] = version+1
        st.toast("已保存传感器配置版本")
        st.rerun()


def evidence_panel(snapshot, channel, sample, threshold, *, gap=False):
    ev = observation(snapshot, channel, sample, threshold, gap=gap)
    if gap:
        st.caption("采样空档。下列证据对应空档前的最后一次观测，当前时刻缺少测量值。")
    st.caption(f"CSV 原始起始行 {ev['csv_line'] or '未知'} · {channel} · "
               f"传感器 {ev['serial'] or '未配置'} · 配置版本 {ev['version'] or '未知'}")
    if ev["synchronized_utc"]:
        st.caption(f"按登记时基对齐的 UTC · {ev['synchronized_utc']}")
    st.json(ev, expanded=False)


def _reference_panel(snapshot, channel, samples, key):
    if not st.toggle("用独立参考数据复核标定", key=key + "_show_reference"):
        return
    binding = active_channels(snapshot).get(channel)
    metadata = binding["metadata"] if binding else {}
    spatial = registration(metadata)
    digest = snapshot.get("source_sha256")
    if not digest:
        st.info("该批次缺少原文件摘要。请保留旧批次并重新导入原文件，才能进行逐行参考比对。")
        return
    if spatial["position"] is None or spatial["direction"] is None or metadata.get("clock_status") != "verified":
        st.info("请在数据导入页登记并核验安装点、模型系测量轴和时间基准，再上传参考数据。")
        return
    st.caption("参考数据应来自同一试次的独立记录。按源 CSV 起始行配对，填入带时区的参考时间和模型系三轴加速度（m/s²）。")
    st.code(digest, language=None)
    st.download_button("下载参考 CSV 表头", ",".join(REFERENCE_COLUMNS) + "\n",
                       file_name="calibration-reference-template.csv", mime="text/csv", key=key + "_template")
    version = binding["version"]
    scope = (key, channel, version, digest, samples[0].get("sample_id"), samples[-1].get("sample_id"), len(samples))
    form_key = f"{key}_{channel}_v{version}_reference"
    result_key = "sensor_last_reference_check"
    with st.form(form_key):
        source = st.text_input("独立参考记录编号", max_chars=200)
        reference = st.file_uploader("独立参考 CSV", type=["csv"])
        limits = st.columns(3)
        acceleration_limit = limits[0].number_input("最大加速度误差（m/s²）", min_value=0.0, value=None)
        time_limit = limits[1].number_input("最大时间误差（ms）", min_value=0.0, value=None)
        excitation = limits[2].number_input("每个方向的最小激励（m/s²）", min_value=0.0, value=None)
        submitted = st.form_submit_button("复核当前标定")
    if submitted:
        st.session_state.pop(result_key, None)
        if reference is None or None in (acceleration_limit, time_limit, excitation):
            st.error("请选择参考 CSV，并按试验要求填写全部三个阈值。")
            return
        try:
            result = check_reference(snapshot, channel, samples, reference.getvalue(), reference_record=source,
                                     acceleration_limit=acceleration_limit, time_limit_ms=time_limit,
                                     min_excitation=excitation)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state[result_key] = (scope, result)
    saved = st.session_state.get(result_key)
    if not saved or saved[0] != scope:
        return
    result = saved[1]
    st.caption(f"本会话最近一次复核 · {binding['serial']} v{version} · {result['reference_record']}")
    if metadata["origin"] == "illustration":
        st.caption("当前传感器配置为虚构教学示意。")
    if result["status"] == "within_limits":
        st.success("所选参考点在设定误差内。")
    elif result["status"] == "outside_limits":
        st.warning("至少一个配对点超出设定误差。请核对测量轴、标定参数和时基。")
    else:
        st.warning("参考数据的双向激励不足，无法确认本次配置符合设定误差。")
    metrics = result["metrics"]
    a, b, c = st.columns(3)
    a.metric("配对点", metrics["pair_count"])
    b.metric("最大加速度偏差", f"{metrics['max_acceleration_error_m_s2']:.4g} m/s²")
    c.metric("最大时间偏差", f"{metrics['max_time_error_ms']:.4g} ms")
    st.dataframe([{"原始 CSV 行": row["csv_line"], "参考投影 m/s²": row["expected_m_s2"],
                   "标定读值 m/s²": row["measured_m_s2"], "读值误差 m/s²": row["error_m_s2"],
                   "时间差 ms": row["delta_ms"]} for row in result["pairs"]], hide_index=True,
                 height=min(220, (metrics["pair_count"] + 1) * 36 + 2))
    st.download_button("下载参考复核证据", json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2),
                       file_name=f"batch-{result['batch_id']}-{channel}-reference-v{version}.json",
                       mime="application/json", key=form_key + "_download")


def batch_panel(vehicle, batch, *, key, analysis=None, threshold=None):
    options = {"key": f"sensor_{key}_{vehicle}_{batch}", "on_change": "rerun"} if _LAZY_EXPANDER else {}
    panel = st.expander("传感器配置与数据溯源", **options)
    if _LAZY_EXPANDER and not panel.open:
        return
    with panel:
        snapshot = read_snapshot(vehicle, batch)
        if not snapshot:
            st.caption("找不到该车辆的批次档案。")
            return
        rows = channel_inventory(snapshot)
        if rows:
            st.dataframe(rows, hide_index=True)
        else:
            st.info("当前批次的安装位置与标定未知。可在数据导入的批次记录中登记传感器。")
        channel = st.selectbox("测量通道", list(CHANNELS), format_func=CHANNELS.get,
                               key=f"{key}_sensor_channel_{vehicle}_{batch}")
        if analysis is not None:
            samples = analysis["samples"]
            i = st.number_input("当前分析范围样本序号", min_value=1, max_value=len(samples), step=1,
                                key=f"{key}_sensor_row_{vehicle}_{batch}")
            evidence_panel(snapshot, channel, samples[i-1], threshold)
            packet = scope_evidence(snapshot, channel, samples, analysis[channel], threshold, "历史导入批次")
            st.download_button("下载本通道分析证据", json.dumps(packet,ensure_ascii=False,indent=2),
                               file_name=f"batch-{batch}-{channel}-evidence.json", mime="application/json")
            binding = active_channels(snapshot).get(channel)
            if supports_frame_projection(binding):
                from rail_vehicle.ui import SIMULATION, navigate
                st.button("按此测量轴查看仿真响应", on_click=navigate, args=(SIMULATION,),
                          kwargs={"frame_sensor_target": (vehicle, batch, channel, binding["version"])})
            _reference_panel(snapshot, channel, samples, f"{key}_{vehicle}_{batch}")
        else:
            _edit(snapshot, channel, f"sensor_{key}_{vehicle}_{batch}")
            if channel in snapshot["channels"]:
                with st.expander("查看修改记录"):
                    st.json(revisions(vehicle,batch,channel), expanded=False)
