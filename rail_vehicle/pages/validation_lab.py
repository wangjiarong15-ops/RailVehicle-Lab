"""Local engineering validation, profile comparison and study exports."""

from dataclasses import replace

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from rail_vehicle.flexible_track import CHANNELS, TrackCase
from rail_vehicle.manchester_contact import DIRECTORY, REFERENCE, run_external_case
from rail_vehicle.profile_geometry import Pair, read_profile, sample_profiles
from rail_vehicle.ui import page_title, plot_chart
from rail_vehicle.validation_center import (CATALOG, LEVELS, Provenance, compare_reference, numerical_reference,
                                             validation_pdf)
from rail_vehicle.validation_uncertainty import study
from rail_vehicle.virtual_trials import json_text
from rail_vehicle.wheel_rail import Contact, isolated_wheelset


@st.cache_data(max_entries=12,show_spinner=False)
def _compare(case,channel,reference,source,tolerance):
    if reference is None:
        source,reference=numerical_reference(case,channel)
    return compare_reference(case,channel,CHANNELS[channel],source,reference,tolerance)


def _catalog():
    for title,url,scope in CATALOG:
        st.markdown(f"**[{title}]({url})**")
        st.caption(scope)
    st.subheader("Manchester 公开截面复核")
    st.caption("复算 CONTACT Case 1 的零横移、零摇头刚性首触基准。原始型面按固定版本取得。")
    st.markdown(f"[获取原始型面]({DIRECTORY}) · [参考求解输出]({REFERENCE})")
    a,b=st.columns(2)
    wheel=a.file_uploader("MBench_S1002_v3.prw",type="prw",max_upload_size=1)
    rail=b.file_uploader("MBench_UIC60_v3.prr",type="prr",max_upload_size=1)
    inputs=(wheel.getvalue(),rail.getvalue()) if wheel is not None and rail is not None else None
    if st.button("复算 Manchester 刚性首触",disabled=inputs is None):
        st.session_state.pop("vi_manchester",None)
        try:
            st.session_state["vi_manchester"]=(inputs,run_external_case(*inputs))
        except (ValueError,ImportError) as error:
            st.warning(str(error))
    if (record:=st.session_state.get("vi_manchester")) and record[0]==inputs:
        report,wheel_csv,rail_csv=record[1]
        st.metric("刚性首触差（mm）",f"{report['rigid_touch_error_mm']:.8f}")
        st.write(f"本模型刚性触点与 CONTACT 加载参考点横向差 {report['lateral_difference_mm']:.4f} mm。"
                 "两者的载荷条件不同，作为模型范围对照展示。")
        a,b,c=st.columns(3)
        a.download_button("下载公开基准证据 JSON",json_text(report),"manchester-contact.json",on_click="ignore")
        b.download_button("导出换算后的轮面",wheel_csv,"manchester-wheel.csv","text/csv",on_click="ignore")
        c.download_button("导出换算后的轨面",rail_csv,"manchester-rail.csv","text/csv",on_click="ignore")


def _benchmark():
    st.subheader("垂向轮轨组件")
    st.caption("轮对质量、钢轨等效质量、轨下支承及单侧 Hertz 接触。比较固定起点和单位的同一通道。")
    first,second,third=st.columns(3)
    pad=first.number_input("轨下刚度（MN/m）",10.,100.,40.)
    depth=second.number_input("局部不平顺深度（mm）",0.,5.,1.5)
    ratio=third.number_input("局部刚度比例",.4,1.4,.8)
    case=replace(TrackCase(),pad_stiffness_n_m=pad*1e6,defect_depth_m=depth/1000,support_factor=ratio)
    source_kind=st.radio("参考方式",("内置 Radau 数值基准","导入授权参考 CSV"),horizontal=True)
    names={"normal_n":"轮轨法向力","wheel_z_m":"轮对垂向位移","rail_z_m":"钢轨垂向位移","wheel_accel_m_s2":"轮对垂向加速度"}
    channel=st.selectbox("对照通道",tuple(names),format_func=names.get)
    unit=CHANNELS[channel]
    blob=metadata=None
    if source_kind=="导入授权参考 CSV":
        reference=st.file_uploader("参考 CSV（time_s,value）",type="csv",max_upload_size=1)
        level=st.selectbox("来源等级",("external","rig","vehicle","numerical","analytic"),format_func=LEVELS.get)
        title=st.text_input("基准来源和试验名",max_chars=180)
        version=st.text_input("数据版本或试验编号",max_chars=180)
        license_note=st.text_input("使用权声明",max_chars=180)
        conditions=st.text_input("工况与时间起点",max_chars=180)
        url=st.text_input("来源链接（HTTPS）",max_chars=400)
        blob=reference.getvalue() if reference is not None else None
        metadata=(level,title,version,license_note,conditions,url)
    threshold=st.number_input(f"声明的 RMSE 门槛（{unit}）",min_value=1e-9,max_value=1e7,
                              value={"N":1.,"m":1e-5,"m/s²":.001}[unit],format="%.8f",key=f"lab_gate_{channel}")
    selection=(case,channel,blob,metadata,threshold)
    if st.button("计算与核对基准",type="primary"):
        st.session_state.pop("vi_benchmark",None)
        try:
            if metadata is not None:
                if blob is None:
                    raise ValueError("请提供基准参考文件。")
                declared=Provenance(*metadata)
            else:
                declared=None
            with st.spinner("正在积分并核对同一时间轴…"):
                st.session_state["vi_benchmark"]=(selection,_compare(case,channel,blob,declared,threshold))
        except (ValueError,ImportError) as error:
            st.warning(str(error))
    result=st.session_state.get("vi_benchmark")
    if result and result[0]==selection:
        report=result[1]
        st.subheader("基准对照结果")
        st.caption(f"{LEVELS[report['source']['level']]} · {report['source']['title']} · {report['sample_count']} 个参考样点")
        metrics=st.columns(3)
        for column,key,label in zip(metrics,("rmse","mae","peak_error"),("RMSE","MAE","峰值误差"),strict=True):
            column.metric(label,f"{report[key]:.6g} {report['unit']}")
        if report["within_declared_gate"]:
            st.success("在本次声明的误差门槛内。")
        else:
            st.warning("超出本次声明的误差门槛。")
        fig=go.Figure()
        fig.add_scatter(x=report["time_s"],y=report["reference"],name="参考结果")
        fig.add_scatter(x=report["time_s"],y=report["response"],name="DOP853 复算",line=dict(dash="dot"))
        fig.update_layout(height=365,xaxis_title="共同时间（s）",yaxis_title=report["unit"])
        plot_chart(fig,key="vi-reference-chart")
        a,b=st.columns(2)
        a.download_button("下载基准证据 JSON",json_text(report),"validation-evidence.json",on_click="ignore")
        b.download_button("下载基准报告 PDF",validation_pdf(report),"validation-report.pdf","application/pdf",on_click="ignore")


@st.cache_data(max_entries=8,show_spinner=False)
def _profile_scan(wheel,rail,radius,flange,span):
    pair=Pair(read_profile(wheel),read_profile(rail),radius,flange/1000)
    return pair.scan(np.linspace(-span/1000,span/1000,61))


def _profiles():
    st.subheader("轮轨型面与接触几何")
    st.caption("横轴由各侧车轮内侧向外，竖轴向上。轮面使用名义滚圆零点，钢轨先换算到同一横向基准。CSV 使用 y_mm,z_mm 或 y_m,z_m。")
    kind=st.radio("型面来源",("虚构新轮与磨耗对照","导入轮面及轨面 CSV"),horizontal=True)
    demo=kind=="虚构新轮与磨耗对照"
    if demo:
        wheel,rail=sample_profiles()
        x,y=st.columns(2)
        x.download_button("下载虚构轮面模板",wheel,"synthetic-wheel.csv","text/csv",on_click="ignore")
        y.download_button("下载虚构轨面模板",rail,"synthetic-rail.csv","text/csv",on_click="ignore")
    else:
        x,y=st.columns(2)
        wheel=x.file_uploader("车轮型面 CSV",type="csv",max_upload_size=1)
        rail=y.file_uploader("钢轨型面 CSV",type="csv",max_upload_size=1)
        if not wheel or not rail:
            st.info("请上传同一截面坐标下的成对型面。")
            return
        wheel,rail=wheel.getvalue(),rail.getvalue()
    x,y,z=st.columns(3)
    radius=x.number_input("名义滚圆半径（m）",.25,.7,.46)
    flange=y.number_input("轮缘分区坐标（mm）",-60.,-1.,-24.)
    span=z.slider("扫描横移范围（±mm）",2,16,8)
    try:
        pair=Pair(read_profile(wheel),read_profile(rail),radius,flange/1000)
        rows=_profile_scan(wheel,rail,radius,flange,span)
        chosen=st.slider("轮对横移（mm）",float(-span),float(span),0.,step=.25)
        point=pair.contact(chosen/1000)
        alt=_profile_scan(*sample_profiles(worn=True),radius,flange,span) if demo else ()
    except (ValueError,ImportError) as error:
        st.warning(str(error))
        return
    fig=go.Figure()
    fig.add_scatter(x=np.asarray(pair.rail.transverse_m)*1000,y=np.asarray(pair.rail.height_m)*1000,name="钢轨")
    fig.add_scatter(x=np.asarray(pair.wheel.transverse_m)*1000+chosen,
                    y=(np.asarray(pair.wheel.height_m)+point["wheel_vertical_shift_m"])*1000,name="左轮")
    fig.add_scatter(x=[point["rail_y_m"]*1000],y=[point["rail_z_m"]*1000],name="最大刚性间隙约束点",mode="markers")
    fig.update_layout(height=355,xaxis_title="截面横向坐标（mm）",yaxis_title="高度（mm）")
    plot_chart(fig,key="vi-profile")
    status={"edge":"截面边界","ambiguous":"近等高候选","flange":"轮缘分区","tread":"踏面分区"}
    st.write(f"左轮候选为{status[point['status']]}，轨面相切方向估计 {point['angle_deg']:.2f}°。")
    fig=go.Figure()
    for data,label in ((rows,"当前型面"),(alt,"虚构磨耗对照")):
        if data:
            fig.add_scatter(x=[v["shift_m"]*1000 for v in data],y=[v["delta_radius_m"]*1000 for v in data],name=label)
    fig.update_layout(height=275,xaxis_title="轮对横移（mm）",yaxis_title="左减右滚动半径差（mm）")
    plot_chart(fig,key="vi-profile-radius")
    st.download_button("导出几何证据 JSON",json_text({"schema":"rail.profile.v1","wheel_sha256":pair.wheel.sha256,
                       "rail_sha256":pair.rail.sha256,"source":"simulated" if demo else "uploaded_unverified",
                       "radius_m":radius,"flange_m":flange/1000,"results":rows}),"profile-geometry.json",on_click="ignore")
    if st.button("使用中心拟合锥度复算独立轮对"):
        try:
            fit=pair.local_conicity()
            run=isolated_wheelset(replace(Contact(),conicity=fit["conicity"],radius_m=radius),20.)
            if max(abs(row[0]) for row in run["state"])>fit["span_m"]:
                raise ValueError("独立轮对超出拟合锥度的横移区间。")
            st.write(f"中心拟合锥度 {fit['conicity']:.5f}，拟合最大残差 {fit['residual_m']*1000:.5f} mm。")
            st.line_chart({"秒":run["time_s"],"轮对横移（mm）":[r[0]*1000 for r in run["state"]]},x="秒")
        except ValueError as error:
            st.warning(str(error))


@st.cache_data(max_entries=4,show_spinner=False)
def _sweep(spread,limit):
    return study(TrackCase(),spread=spread,load_min_ratio=limit)


def _uncertainty():
    st.subheader("参数扰动与候选复算")
    st.caption("同一组虚构参数扰动分别施加于基准和轨下阻尼加 25% 的候选。轮重比门槛由本次试验声明。")
    spread=st.slider("参数扰动上限（%）",5,25,12)
    limit=st.slider("最小轮重／静载",.40,.98,.75)
    if st.button("运行配对抽样",type="primary"):
        try:
            with st.spinner("正在完成 12 个配对工况和最不利索引复算…"):
                st.session_state["vi_uncertainty"]=_sweep(spread/100,limit)
        except (ValueError,ImportError) as error:
            st.warning(str(error))
    if (report:=st.session_state.get("vi_uncertainty")) and (report["spread"],report["load_ratio_limit"])==(spread/100,limit):
        lo,med,hi=report["empirical_reduction_n"]
        st.metric("候选的约束失效比例",f"{report['candidate_failure_rate']:.1%}")
        st.write(f"轮重波动 RMS 减少量的经验 5／50／95 分位数为 {lo:.3f}／{med:.3f}／{hi:.3f} N。"
                 f"独立 Radau 复算差 {report['independent']['difference_n']:.6g} N。")
        st.write("收益通过本次抽样判据。" if report["supported_on_draws"] else "本次抽样不足以支持稳健改善。")
        flat=[{key:value for key,value in row.items() if key!="parameters"} for row in report["rows"]]
        st.dataframe(flat,hide_index=True)
        st.download_button("下载不确定性证据 JSON",json_text(report),"validation-uncertainty.json",on_click="ignore")


def render():
    page_title("工程验证中心","对照已登记的参考、求解结果与有效时钟。")
    selected=st.radio("工程验证任务",("基准对照","轮轨型面","不确定性","外部资料"),horizontal=True)
    if selected=="基准对照":
        _benchmark()
    elif selected=="轮轨型面":
        _profiles()
    elif selected=="不确定性":
        _uncertainty()
    else:
        _catalog()
