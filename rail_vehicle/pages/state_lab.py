"""Acquisition and causal state estimation for a single vertical component."""

from dataclasses import asdict
import csv
import io
import json
import sqlite3

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.measurement_stream import Device, Replay, read_packet_csv, read_recording
from rail_vehicle.acquisition_store import append_packets, load_recording, open_recording, recordings
from rail_vehicle.state_monitoring import ObserverCase, diagnosis, estimate, simulated_stream
from rail_vehicle.ui import COPILOT, navigate, plot_chart
from rail_vehicle.virtual_trials import json_text

SOURCES={"simulated":"虚构模拟采集","declared_field":"声明实车来源，待独立核验",
         "declared_test":"声明试验来源，待独立核验","unknown":"来源未确认"}
DEMO_CASES={"damping":"等效阻尼变化","stiffness":"等效刚度变化","bias":"加速度零偏","nominal":"名义模型"}


def monitoring_intent(text):
    return any(n in text for n in ("数据流","采集记录","实时采集","监控记录")) and any(
        action in text for action in ("异常","残差","核查","诊断","退化"))


def render_evidence(report):
    st.subheader("监测证据与替代解释")
    st.caption(f"{SOURCES[report['source']]} · {report['evidence_id']}")
    if report["status"]=="insufficient":
        st.warning("当前同步或激励证据不足，暂缓给出候选排序。")
    else:
        st.dataframe([{"待验证假设":v["hypothesis"],"留出窗口 RMSE（m/s²）":v["rmse_m_s2"],
                       "样点":v["samples"]} for v in report["attempts"]],hide_index=True)
        if report["status"]=="candidate":
            st.info("首选假设在留出窗口中与记录更接近。仍需在独立条件下核验。")
        else:
            st.info("候选之间的误差差异低于当前测量噪声水平。")
        if report["frequency_hz"] is not None:
            st.write(f"最长连续记录的 Welch 主峰为 {report['frequency_hz']:.3f} Hz。")
    st.write(report["next_action"])
    st.download_button("下载监测证据 JSON",json_text(report),"state-monitoring.json",on_click="ignore")


def _raw_device_controls():
    with st.expander("离线设备记录及登记表"):
        st.caption("JSONL 首行登记设备编号、通道单位、轴向、增益、偏置、共同触发和时钟偏移。CSV 使用相同登记表。")
        sample,_,_=simulated_stream("nominal",20)
        st.download_button("下载虚构设备登记示例",sample.recording().splitlines()[0],
                           "simulated-devices.json","application/json",on_click="ignore")
        jsonl=st.file_uploader("原始设备 JSONL",type="jsonl",max_upload_size=2,key="vi_record")
        csv_file=st.file_uploader("原始设备 CSV",type="csv",max_upload_size=1,key="vi_packet_csv")
        manifest=st.file_uploader("设备登记 JSON",type="json",max_upload_size=1,key="vi_packet_manifest")
        if st.button("读取离线设备记录",disabled=jsonl is None and (csv_file is None or manifest is None)):
            try:
                if jsonl is not None:
                    record=read_recording(jsonl.getvalue())
                else:
                    meta=json.loads(manifest.getvalue())
                    if meta.get("schema")!="rail.acquisition.v1":
                        raise ValueError("设备登记版本无效。")
                    record=read_packet_csv(csv_file.getvalue(),[Device(**item) for item in meta["devices"]],
                                           meta["origin_us"],meta["source"])
                if not record.samples:
                    raise ValueError("没有可使用的同步观测。请核对设备编号、共同触发和校准。")
                st.session_state["vi_recording"]=record
                st.session_state.pop("vi_replay",None)
                st.session_state.pop("vi_truth",None)
                st.session_state.pop("vi_diagnosis",None)
            except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as error:
                st.warning(f"无法导入设备记录。{error}" if isinstance(error,ValueError) else "设备登记格式无效。")


def _state_geometry(row):
    amount=row["estimate_m"]*1000 if row["estimate_m"] is not None else None
    fig=go.Figure()
    fig.add_scatter3d(x=[0,0],y=[0,0],z=[-40,40],mode="lines",name="支座高度坐标")
    if amount is not None:
        sigma=row["sigma_m"]*1000
        fig.add_scatter3d(x=[0,0],y=[0,0],z=[amount-1.96*sigma,amount+1.96*sigma],
                          mode="lines",line=dict(width=10,color="#3B82F6"),name="条件区间")
        fig.add_scatter3d(x=[0],y=[0],z=[amount],mode="markers",marker=dict(size=8,color="#14b8a6"),
                          name="估计位置")
    fig.update_layout(height=285,margin=dict(l=0,r=0,t=4,b=0),showlegend=False,
                      scene=dict(zaxis=dict(title="Z（mm）",range=[-65,65]), xaxis=dict(visible=False),
                                 yaxis=dict(visible=False),aspectratio=dict(x=.35,y=.35,z=1),
                                 aspectmode="manual",camera=dict(eye=dict(x=1.4,y=1.7,z=.3))))
    plot_chart(fig,key="vi-observer-pose")


def _monitor(record,replay,truth,settings):
    if replay is not None:
        a,b,c=st.columns(3)
        if a.button("连接或恢复",disabled=replay.connected or replay.cursor==len(replay.groups)):
            replay.connected=True
            st.rerun()
        if b.button("模拟断线",disabled=not replay.connected):
            replay.connected=False
            st.rerun()
        running=replay.connected
        places=replay.poll(12)
        if running and not replay.connected:
            st.rerun()
        c.metric("采集进度",f"{places} / {len(replay.groups)}")
        st.caption("已连接" if replay.connected else "已结束" if places==len(replay.groups) else "连接暂停")
    st.caption(SOURCES[record.source])
    if not record.samples:
        st.info("等待具有已声明时基和测量轴向的采样。")
        return
    stats=st.columns(4)
    for col,key,name in zip(stats,("accepted","missing_sequence","unsynchronized","invalid_range"),
                           ("有效记录","序号缺口","时钟拒绝","量程或标定拒绝"),strict=True):
        col.metric(name,record.counters[key])
    ticks = sorted(record.samples)
    frequency = (len(ticks) - 1) * 50 / (ticks[-1] - ticks[0]) if len(ticks) >= 2 else None
    rate = f" · 有效帧率 {frequency:.2f} Hz" if frequency is not None else ""
    st.caption(f"原始包 {len(record.packets):,} · 重复 {record.counters['repeated']:,} · "
               f"迟到 {record.counters['late']:,}{rate}")
    try:
        stamp=(len(record.packets),settings)
        saved=st.session_state.get("vi_estimates")
        if not saved or saved[0] is not record or saved[1]!=stamp:
            saved=(record,stamp,estimate(record,settings))
            st.session_state["vi_estimates"]=saved
        rows=saved[2]
    except (ValueError,ImportError) as error:
        st.warning(str(error))
        return
    current=len(rows)-1
    if current and (replay is None or not replay.connected):
        current=st.slider("回放采样点",0,current,current,key=f"vi_seek_{record.evidence_id[:21]}")
    selection=rows[current]
    low=max(0,min(current-250,len(rows)-500))
    visible=rows[low:low+500]
    trace,pose=st.columns([3,1],gap="medium")
    with trace:
        fig=go.Figure()
        for key,name in (("observed_m","原始观测（单位换算）"),("predicted_m","名义模型预测"),("estimate_m","滤波估计")):
            fig.add_scatter(x=[row["time_s"] for row in visible],
                            y=[row[key]*1000 if row[key] is not None else None for row in visible],name=name)
        if truth is not None:
            fig.add_scatter(x=[row["time_s"] for row in visible],y=np.asarray(truth[low:low+len(visible)])*1000,
                            name="虚构真值",line=dict(dash="dot"))
        fig.add_vline(x=selection["time_s"],line_width=1,line_dash="dash")
        fig.update_layout(height=350,xaxis_title="共同触发后的时间（s）",yaxis_title="垂向位移（mm）")
        plot_chart(fig,key="vi-observed-and-filtered")
    with pose:
        st.markdown("**垂向状态坐标**")
        _state_geometry(selection)
        if selection["estimate_m"] is None:
            st.caption("此时刻状态证据不足。")
        else:
            st.caption(f"{selection['time_s']:.2f} s · 滤波位移 {1000*selection['estimate_m']:.2f} mm，"
                       f"模型标准差 {1000*selection['sigma_m']:.2f} mm。")
    st.caption("条件区间取滤波协方差。当前三维标记只表示有证据的单组件垂向位置。")
    if truth is not None:
        expected=np.asarray(truth[:len(rows)])
        metrics=[]
        for channel,label in (("observed_m","原始观测"),("estimate_m","滤波估计")):
            present=[(i,row[channel]) for i,row in enumerate(rows) if row[channel] is not None]
            if present:
                indices,values=zip(*present,strict=True)
                rmse=np.sqrt(np.mean((np.asarray(values)-expected[list(indices)])**2))
                metrics.append(f"{label} RMSE {rmse*1000:.2f} mm")
        if metrics:
            st.caption("以虚构真值单独核验 · "+" · ".join(metrics))
    if replay is not None and replay.connected:
        return
    st.dataframe([asdict(device) for device in record.devices.values()],hide_index=True)
    a,b,c=st.columns(3)
    a.download_button("导出原始 JSONL",record.recording(),"acquisition.jsonl",on_click="ignore")
    target=io.StringIO(newline="")
    writer=csv.DictWriter(target,fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    b.download_button("导出估计时程 CSV",target.getvalue(),"estimated-state.csv","text/csv",on_click="ignore")
    if c.button("比较异常假设",type="primary"):
        st.session_state["vi_diagnosis"]=diagnosis(record,settings)
    st.button("将当前采集记录交给 Copilot",on_click=navigate,args=(COPILOT,),
              kwargs={"cp_vi_recording":record.evidence_id,"cp_vi_observer":settings})
    if (report:=st.session_state.get("vi_diagnosis")) and report["recording"]==record.evidence_id and report["settings"]==asdict(settings):
        render_evidence(report)


@st.fragment(run_every=.35)
def _live(record,replay,truth,settings):
    _monitor(record,replay,truth,settings)


@st.fragment(run_every=.9)
def _bridge():
    state = st.session_state
    capture = state["x_capture"]
    if st.button("暂停本机接收"):
        state["x_capture_active"] = False
        st.rerun()
    try:
        status = capture.receive(state["x_bridge_url"], budget=120)
        if capture.record is not None:
            if not state.get("x_recording_id"):
                state["x_recording_id"] = open_recording("本机采集桥", capture.record)
                state["x_recording_offset"] = 0
            offset = state["x_recording_offset"]
            if len(capture.record.packets) > offset:
                state["x_recording_offset"] = append_packets(state["x_recording_id"], offset, capture.record)
            state["vi_recording"] = capture.record
        state["x_capture_interrupts"] = state.get("x_capture_interrupts", 0) + 1 if status == "disconnected" else 0
        st.caption(f"已持久化 {state.get('x_recording_offset', 0):,} 个包 · "
                   f"连续断线 {state['x_capture_interrupts']} 次")
        if capture.record is not None:
            recent = sorted(capture.record.samples.items())[-200:]
            acceleration = [(tick / 50, values["acceleration"]) for tick, values in recent
                            if "acceleration" in values]
            st.caption(SOURCES[capture.record.source])
            if acceleration:
                time_s, values = zip(*acceleration, strict=True)
                st.metric("最新垂向加速度（m/s²）", f"{values[-1]:.4f}")
                figure = go.Figure(go.Scatter(x=time_s, y=values, mode="lines"))
                figure.update_layout(height=230, xaxis_title="共同触发时间（s）", yaxis_title="垂向加速度（m/s²）")
                plot_chart(figure, key="x_live_raw")
        if status == "complete" or state["x_capture_interrupts"] >= 3:
            state["x_capture_active"] = False
            state["x_capture_notice"] = "采集已结束。" if status == "complete" else "采集桥断线，已保留最近的保存位置。"
            st.rerun()
    except (ImportError, ValueError, OSError, sqlite3.Error, TimeoutError):
        state["x_capture_active"] = False
        state["x_capture_notice"] = "采集已停止。请核对本机采集桥、声明和保存位置后再试。"
        st.rerun()


def _capture_controls():
    from rail_vehicle.stream_loopback import LoopbackCapture

    state = st.session_state
    with st.expander("持久化采集与恢复"):
        st.caption("本机采集桥使用登记后的 50 Hz 垂向通道。原始包与接收位置保存到本机 SQLite。")
        endpoint = st.text_input("本机 WebSocket 采集桥", placeholder="ws://127.0.0.1:8765", max_chars=180)
        active = state.get("x_capture_active", False)
        if st.button("连接或续传本机采集", disabled=active or not endpoint):
            identifier = state.get("x_recording_id")
            try:
                if identifier and endpoint == state.get("x_bridge_url"):
                    record = load_recording(identifier)
                    state["x_recording_offset"] = len(record.packets)
                else:
                    record = None
                    state.pop("x_recording_id", None)
                    state.pop("x_recording_offset", None)
                    state.pop("vi_recording", None)
                state.update(x_capture=LoopbackCapture(record), x_bridge_url=endpoint, x_capture_active=True,
                             x_capture_interrupts=0)
                for key in ("vi_replay", "vi_truth", "vi_diagnosis", "x_capture_notice"):
                    state.pop(key, None)
                st.rerun()
            except (ValueError, sqlite3.Error, OSError):
                st.warning("无法读取已保存的采集位置。")
        if notice := state.get("x_capture_notice"):
            st.caption(notice)
        if active:
            _bridge()
            return
        # These controls run only when the recorder is stopped; open archives are immutable until explicit continuation.
        try:
            archive = {item["id"]: item for item in recordings()}
            if archive:
                selected = st.selectbox("历史采集记录", list(archive),
                                        format_func=lambda key: f"{archive[key]['label']} · {archive[key]['packet_count']} 包 · "
                                                                f"{archive[key]['created_at']} UTC")
                if st.button("恢复历史采集"):
                    record = load_recording(selected)
                    state.update(vi_recording=record, x_recording_id=selected,
                                 x_recording_offset=len(record.packets), x_bridge_url=endpoint)
                    for key in ("vi_replay", "vi_truth", "vi_diagnosis"):
                        state.pop(key, None)
            record = state.get("vi_recording")
            if record is not None and record.packets and st.button("将当前原始记录保存为新档案"):
                identifier = open_recording("当前采集记录", record)
                append_packets(identifier, 0, record)
                st.success("原始包已写入独立采集档案。")
        except (ValueError, sqlite3.Error, OSError):
            st.warning("本机采集档案暂时无法读取或写入。")


def render():
    st.subheader("在线采集与状态估计")
    s=st.session_state
    choice=st.selectbox("虚构激励试验",tuple(DEMO_CASES),format_func=DEMO_CASES.get)
    if st.button("建立本地模拟采集",type="primary"):
        record,packets,truth=simulated_stream(choice)
        s["vi_recording"],s["vi_replay"],s["vi_truth"]=record,Replay(record,packets),truth
        s.pop("vi_diagnosis",None)
    _raw_device_controls()
    _capture_controls()
    if (record:=s.get("vi_recording")) is None:
        st.info("建立模拟采集，或读取带设备登记的离线文件。")
        return
    with st.expander("测量与过程噪声"):
        a,b,c=st.columns(3)
        displacement=a.number_input("位移测量标准差（mm）",.01,30.,1.2)
        acceleration=b.number_input("加速度测量标准差（m/s²）",.001,5.,.08,format="%.3f")
        process=c.number_input("等效过程噪声谱密度（m²/s³）",.001,10.,.08,format="%.3f")
    settings=ObserverCase(process_psd=process,displacement_sigma_m=displacement/1000,acceleration_sigma_m_s2=acceleration)
    replay=s.get("vi_replay")
    view=_live if replay and replay.connected else _monitor
    view(record,replay,s.get("vi_truth"),settings)
