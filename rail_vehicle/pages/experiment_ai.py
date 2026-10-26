"""Opt-in interpretation of precomputed experiments through shared AI services."""

from datetime import datetime, timezone

import streamlit as st
from rail_vehicle.deployment import public_demo_enabled

from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.experiment_context import build_ml_experiment_payload, build_vibration_experiment_payload
from rail_vehicle.ai.fleet_context import build_fleet_comparison_payload
from rail_vehicle.ai.openai_provider import create_configured_provider
from rail_vehicle.ai.ui import (
    ai_configuration_message, clear_result, invalidate_stale_result,
    render_validated_result, request_ai_analysis, selection_fingerprint, status_message,
)


def render_experiment_ai(saved, fingerprint, selection, kind):
    """Never call a provider before an explicit click; cache only verified output."""
    key = f"experiment_{kind}_ai"
    if public_demo_enabled():
        clear_result(st.session_state, key)
        st.info("公开演示模式不调用 AI 服务；本地实验分析与 PDF 导出仍可使用。")
        return None, None
    if not saved or saved.get("fingerprint") != fingerprint:
        clear_result(st.session_state, key)
        return None, None
    st.subheader("🤖 多车辆 AI 辅助解读" if kind == "fleet" else "🤖 实验 AI 辅助解读")
    st.caption("AI 生成内容，仅解释当前聚合指标，不构成故障、事故、安全、法规或标准舒适等级结论。证据范围校验不证明自然语言推理正确。")
    st.info("仅在主动点击后发送匿名批次聚合指标、速度区间和可比性限制；不发送车辆编号、备注、文件名或原始样本。本阶段不导出多车辆 AI PDF。" if kind == "fleet" else "仅在主动点击后发送已计算的聚合指标、参数、时间范围和局限；不发送原始 CSV、样本、分数序列、频谱数组或 PDF。当前完整验证的解读可附加到实验 PDF。")
    st.caption("证据范围：B1、B2… 按页面车辆顺序各对应一辆车；公共筛选参数不归属于单一批次。" if kind == "fleet" else "证据范围：B1 为参考批次，B2 为检测批次；模型参数不归属于单个批次。" if kind == "ml" else "证据范围：B1 为当前筛选后的振动分析记录。")
    try:
        builder = {"ml": build_ml_experiment_payload, "vibration": build_vibration_experiment_payload, "fleet": build_fleet_comparison_payload}[kind]
        payload = builder(saved["result"])
        local_selection = dict(selection=selection, analysis_fingerprint=fingerprint)
        invalidate_stale_result(st.session_state, key, payload, local_selection)
    except (ValueError, TypeError, KeyError, OverflowError):
        clear_result(st.session_state, key)
        st.warning("当前对比结果不符合 AI 输入要求，未调用服务；确定性对比仍可使用。" if kind == "fleet" else "当前实验结果不符合 AI 输入要求，未调用服务；实验报告仍可使用。")
        return None, None
    try:
        config = AIConfig.from_sources()
    except Exception:
        clear_result(st.session_state, key)
        st.warning("无法读取 AI 配置，未调用服务；确定性对比仍可使用。" if kind == "fleet" else "无法读取 AI 配置，未调用服务；实验分析与报告仍可使用。")
        return None, None
    unavailable = ai_configuration_message(config)
    if unavailable:
        clear_result(st.session_state, key)
        st.warning(unavailable)
    with st.expander("本次允许引用的多车辆聚合指标" if kind == "fleet" else "本次允许引用的实验聚合指标", expanded=False):
        from rail_vehicle.ai.schemas import collect_evidence_metadata
        facts = {item["evidence_id"]: item for item in payload["metrics"]}
        for batch in payload.get("batches", []):
            facts.update({item["evidence_id"]: item for item in batch["metrics"]})
        rows = [{**item, **{field: facts[item["evidence_id"]][field] for field in ("name", "value", "unit", "method")}}
                for item in collect_evidence_metadata(payload)]
        if kind == "fleet":
            st.table(rows)
        else:
            st.dataframe(rows, hide_index=True, width="stretch")
    if st.button("🤖 生成多车辆 AI 解读" if kind == "fleet" else "🤖 生成实验 AI 解读", key=f"{kind}_experiment_ai_generate", disabled=unavailable is not None):
        clear_result(st.session_state, key)
        with st.spinner("正在解读实验聚合结果……"):
            result = request_ai_analysis(payload, config, has_data=True, provider_factory=create_configured_provider)
        if result.get("status") == "available" and result.get("scope_validation") == "verified":
            result["selection_fingerprint"] = selection_fingerprint(local_selection)
            result["ai_metadata"] = dict(provider=config.provider, model=config.model,
                                         generated_at=datetime.now(timezone.utc).isoformat())
            st.session_state[key] = result
        else:
            st.warning(status_message(result))
    result = st.session_state.get(key)
    # Experiment contexts are new: there is no legacy missing/null scope path.
    if result is not None:
        if result.get("status") != "available" or result.get("scope_validation") != "verified":
            clear_result(st.session_state, key)
            st.warning("实验 AI 证据范围未完整验证，未展示结果。")
        elif not render_validated_result(result, payload, st):
            clear_result(st.session_state, key)
    return payload, st.session_state.get(key)
