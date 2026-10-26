"""Optional, local machine-learning anomaly exploration."""

import sqlite3

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from rail_vehicle.deployment import demo_source_options

from rail_vehicle.demo_data import ml_demo_samples
from rail_vehicle.ml_anomaly import (
    MLUnavailableError, MLValidationError, STATE_KEY, analysis_fingerprint, detect_ml_anomalies, estimator_class,
)
from rail_vehicle.run_data import list_batch_samples, list_vehicle_batches
from rail_vehicle.vehicle_data import list_vehicles
from rail_vehicle.pages.experiment_export import render_export
from rail_vehicle.pages.experiment_ai import render_experiment_ai
from rail_vehicle.pages.ml_review import render_review


CATEGORY_LABELS = dict(both="两者均标记", ml_only="仅模型标记", rule_only="仅规则标记", neither="两者均未标记")


def build_score_chart(result):
    records = result["records"]
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.13)
    # Limit rendering size only; every sample remains in model scoring and tables.
    step = max(1, (len(records) + 1999) // 2000)
    visible = records[::step]
    figure.add_trace(go.Scatter(x=[r["timestamp"] for r in visible],
                               y=[r["ml_score"] for r in visible], name="模型离群分数",
                               mode="lines", line_color="#2563eb"), row=1, col=1)
    figure.add_hline(y=result["score_threshold"], row=1, col=1, line_dash="dash", line_color="#dc2626")
    for category, color, symbol in (("both", "#dc2626", "diamond"),
                                     ("ml_only", "#7c3aed", "circle"),
                                     ("rule_only", "#d97706", "x")):
        marked = [r for r in records if r["category"] == category]
        figure.add_trace(go.Scatter(x=[r["timestamp"] for r in marked],
                                   y=[r["ml_score"] for r in marked],
                                   mode="markers", name=CATEGORY_LABELS[category],
                                   marker=dict(color=color, size=8, symbol=symbol)), row=1, col=1)
    figure.add_trace(go.Scatter(x=[r["timestamp"] for r in visible],
                               y=[r["speed_kmh"] for r in visible],
                               name="速度", line_color="#0891b2"), row=2, col=1)
    figure.update_yaxes(title_text="离群分数（非概率）", row=1, col=1)
    figure.update_yaxes(title_text="速度（km/h）", row=2, col=1)
    figure.update_xaxes(title_text="样本时间（UTC）", row=2, col=1)
    figure.update_layout(height=550, margin=dict(l=20, r=20, t=25, b=20),
                         legend=dict(orientation="h", y=1.1))
    return figure


def render():
    st.header("机器学习异常检测")
    st.info("本地 Isolation Forest · 学习参考批次分布，探索待检测数据中的离群样本。结果不代表故障或安全判断。")
    st.caption("仅在点击“训练并检测”后进行本地计算；模型计算不调用 AI API，不上传样本，不保存模型文件。可另行主动请求聚合指标的 AI 解读。")
    try:
        estimator_class()
    except MLUnavailableError:
        st.session_state.pop(STATE_KEY, None)
        st.warning("机器学习可选依赖尚未安装，其他功能仍可使用。")
        st.code('.venv\\Scripts\\python.exe -m pip install -e ".[ml]"', language="powershell")
        return
    source = st.radio("检测数据来源", demo_source_options("历史运行批次", "内置虚构实验"), horizontal=True, key="ml_source")
    try:
        if source == "内置虚构实验":
            reference, target = ml_demo_samples()
            selection = dict(source="demo")
            st.caption("独立生成的 400 条参考样本与 160 条待检测样本。待检测数据第 40–45 秒加入速度分布变化，第 100–104 秒加入加速度波动；这些设定不作为模型输入标签。")
        else:
            vehicles = list_vehicles()
            if not vehicles:
                st.session_state.pop(STATE_KEY, None)
                st.info("请先登记车辆并导入 CSV，或选择“内置虚构实验”。")
                return
            vehicle = st.selectbox("检测车辆", [v["id"] for v in vehicles],
                                   format_func=lambda vid: next(v["vehicle_code"] for v in vehicles if v["id"] == vid), key="ml_vehicle")
            batches = list_vehicle_batches(vehicle)
            if len(batches) < 2:
                st.session_state.pop(STATE_KEY, None)
                st.info("独立参考检测至少需要同一车辆的两个历史批次，也可直接体验内置虚构实验。")
                return
            labels = {b["id"]: f"批次 {b['id']} · {b['file_name']} · {b['sample_count']} 条" for b in batches}
            reference_id = st.selectbox("参考批次（仅用于训练）", list(labels), format_func=labels.get, key=f"ml_reference_{vehicle}")
            target_id = st.selectbox("待检测批次（不参与训练）", [bid for bid in labels if bid != reference_id],
                                     format_func=labels.get, key=f"ml_target_{vehicle}_{reference_id}")
            reference = list_batch_samples(vehicle, reference_id)
            target = list_batch_samples(vehicle, target_id)
            selection = dict(source="history", vehicle=vehicle, reference=reference_id, target=target_id)
            st.warning("请自行选择工况可比且具有代表性的参考批次。批次未经故障标签确认，模型学习的是数据分布，不能把参考数据视为已认证的正常状态。")
        columns = st.columns(2)
        contamination = columns[0].slider("参考离群比例（模型阈值参数）", 0.01, 0.20, 0.05, 0.01, key="ml_contamination")
        rule_threshold = columns[1].number_input("规则加速度阈值（m/s²）", min_value=0.0, value=1.0, step=0.1, key="ml_rule_threshold")
        st.caption("参考离群比例用于从参考分数确定模型阈值，不表示待检测批次的真实异常率；规则仍为任一加速度绝对值严格大于阈值。")
        fingerprint = analysis_fingerprint(reference, target, contamination=contamination,
                                           rule_threshold=rule_threshold, selection=selection)
    except (ValueError, TypeError, KeyError, OverflowError):
        st.session_state.pop(STATE_KEY, None)
        st.error("所选数据无法用于检测，请检查批次数据和参数。")
        return
    except (sqlite3.Error, OSError):
        st.session_state.pop(STATE_KEY, None)
        st.error("无法读取历史批次，请稍后重试。")
        return
    saved = st.session_state.get(STATE_KEY)
    if saved and saved["fingerprint"] != fingerprint:
        st.session_state.pop(STATE_KEY, None)
        saved = None
    if st.button("训练并检测", type="primary", key="ml_train"):
        st.session_state.pop(STATE_KEY, None)
        try:
            with st.spinner("正在本地训练参考模型并检测……"):
                result = detect_ml_anomalies(reference, target, contamination=contamination, rule_threshold=rule_threshold)
            saved = dict(fingerprint=fingerprint, result=result)
            st.session_state[STATE_KEY] = saved
        except (MLValidationError, MLUnavailableError) as exc:
            st.warning(str(exc))
            return
        except Exception:
            st.error("本地模型运行失败，未展示结果；原有统计分析和报告功能仍可使用。")
            return
    if not saved:
        st.session_state.pop("experiment_ml_ai", None)
        st.info("数据已选择。点击“训练并检测”生成本地结果。")
        return
    result = saved["result"]
    st.subheader("模型与规则结果对比")
    columns = st.columns(4)
    for col, label, value in zip(columns, ("检测样本数", "模型标记样本", "规则标记样本", "两者均标记"),
                                  (result["sample_count"], result["ml_count"], result["rule_count"], result["counts"]["both"])):
        col.metric(label, value, border=True)
    st.dataframe([dict(分类=CATEGORY_LABELS[key], 样本数=value,
                       占比=f"{value / result['sample_count']:.2%}") for key, value in result["counts"].items()],
                  hide_index=True, width="stretch")
    st.plotly_chart(build_score_chart(result), width="stretch", key="ml_score_chart")
    st.caption("分数越高表示相对参考分布越容易被孤立，不是故障概率。虚线为参考数据学习得到的分数阈值；同分样本使用严格大于比较。曲线底线最多绘制约 2,000 个点，异常标记和统计使用全部记录。")
    st.subheader("需进一步查看的样本")
    flagged = [dict(时间_UTC=r["timestamp"], 速度_kmh=r["speed_kmh"],
                    横向加速度_ms2=r["lateral_accel"], 垂向加速度_ms2=r["vertical_accel"],
                    模型离群分数=round(r["ml_score"], 6), 分类=CATEGORY_LABELS[r["category"]])
               for r in result["records"] if r["category"] != "neither"]
    if flagged:
        st.dataframe(flagged, hide_index=True, width="stretch")
    else:
        st.info("当前参数下，模型与规则均未标记样本。")
    render_review(result, fingerprint)
    with st.expander("实验记录与局限", expanded=False):
        st.write(dict(算法="scikit-learn IsolationForest", 特征=result["features"],
                      参考样本数=result["reference_count"], 检测样本数=result["sample_count"],
                      参考时间范围=[result["reference_start"], result["reference_end"]],
                      检测时间范围=[result["target_start"], result["target_end"]],
                      树数量=result["tree_count"], 随机种子=result["seed"],
                      参考离群比例=result["contamination"], 模型分数阈值=result["score_threshold"]))
        st.caption("仅使用速度和双轴瞬时加速度，未建模频谱、线路、载荷或车辆故障机理。参考与检测工况不同会产生分布偏移和误报。无真实故障标签，不能据此宣称真实诊断准确率；模型未标记也不代表安全。单个批次最多支持 20,000 条。")
    ai_context, ai_result = render_experiment_ai(saved, fingerprint, selection, "ml")
    render_export(saved, fingerprint, selection, "ml", ai_context=ai_context, ai_result=ai_result)
