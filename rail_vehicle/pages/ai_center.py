"""Opt-in AI interpretation of validated, session-only analysis contexts."""

from dataclasses import replace
from datetime import datetime, timezone

import streamlit as st

from rail_vehicle.ai.center_state import CONTEXTS, current_context
from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.evidence_view import evidence_detail_catalog
from rail_vehicle.ai.openai_provider import create_configured_provider
from rail_vehicle.ai.ui import (ai_configuration_message, clear_result, invalidate_stale_result,
                               render_validated_result, request_ai_analysis, selection_fingerprint,
                               status_message)
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.ui import navigate, page_title


def render():
    page_title("AI 智能分析中心", "仅解释当前会话中已计算的结构化结果")
    st.caption("AI 内容仅供辅助参考，不构成故障、事故、安全、法规或标准舒适等级结论。")
    public = public_demo_enabled()
    if public:
        st.info("公开演示模式不读取私人分析上下文，也不调用 AI 服务；请使用各页面的离线演示。")
        return
    available = {kind: item for kind in CONTEXTS if (item := current_context(st.session_state, kind)) is not None}
    if not available:
        st.info("当前会话没有可用的分析结果。请先在运行分析、振动、机器学习或多车辆页面完成分析。")
        if public:
            st.caption("公开演示模式禁止调用 AI 服务。")
        return
    left, middle, right = st.columns([1.1, 2.2, 1.4])
    with left:
        st.subheader("分析对象")
        requested = st.session_state.pop("ai_center_requested_kind", None)
        if requested in available:
            st.session_state["ai_center_kind"] = requested
        if st.session_state.get("ai_center_kind") not in available:
            st.session_state["ai_center_kind"] = next(iter(available))
        kind = st.selectbox("分析类型", list(available), format_func=lambda value: CONTEXTS[value][0], key="ai_center_kind")
        destination = {"single": "运行分析", "batch": "运行分析", "ml": "机器学习异常检测",
                       "vibration": "振动与平稳性分析", "fleet": "多车辆运行对比"}[kind]
        st.button("返回分析页面", on_click=navigate, args=(destination,))
        item = available[kind]
        payload, selection = item["payload"], item["selection"]
        key = CONTEXTS[kind][2]
        st.caption("只保存在当前会话；更改源页面的选择或参数后，需重新确认当前结果。")
        try:
            config = AIConfig.from_sources()
        except Exception:
            st.warning("AI 配置读取失败，请检查本地配置；离线分析和报告仍可使用。")
            config = AIConfig(enabled=False)
        st.caption(f"Provider：{config.provider}；密钥仅从本机配置读取，不在页面显示。")
        model_key = f"ai_center_model_{config.provider}"
        source_key = f"ai_center_config_model_{config.provider}"
        # Keep manual edits on rerun, but do not keep using a stale model after
        # the underlying configuration changes during the same session.
        if model_key not in st.session_state or (source_key in st.session_state and st.session_state[source_key] != config.model):
            st.session_state[model_key] = config.model
        st.session_state[source_key] = config.model
        model = st.text_input("模型名称", key=model_key)
        timeout = st.number_input("超时（秒）", min_value=1, max_value=300,
                                  value=int(config.timeout), key="ai_center_timeout")
        config = replace(config, model=model.strip(), timeout=float(timeout))
        unavailable = ai_configuration_message(config)
        if unavailable:
            st.warning(unavailable)
        else:
            st.success("AI 服务已配置；仅在主动点击后请求。")
        st.caption("Provider 和密钥通过本地配置管理；模型与超时调整仅作用于当前会话。")
    with middle:
        st.subheader("辅助解读")
        st.info("仅发送已计算的聚合指标与异常事件，不发送原始 CSV、样本、备注或 PDF。")
        failure_key = "ai_center_last_failure"
        failure_identity = (kind, item["input_fingerprint"], item["selection_fingerprint"], config.provider, config.model, config.timeout)
        previous_failure = st.session_state.get(failure_key)
        if unavailable or not isinstance(previous_failure, dict) or previous_failure.get("identity") != failure_identity:
            st.session_state.pop(failure_key, None)
        invalidate_stale_result(st.session_state, key, payload, selection)
        previous = st.session_state.get(key)
        metadata = previous.get("ai_metadata", {}) if isinstance(previous, dict) else {}
        if unavailable or (metadata and (metadata.get("provider") != config.provider or metadata.get("model") != config.model)):
            clear_result(st.session_state, key)
        if st.button("生成 AI 辅助解读", key="ai_center_generate", type="primary", disabled=bool(unavailable)):
            clear_result(st.session_state, key)
            st.session_state.pop(failure_key, None)
            with st.spinner("正在解读已计算结果……"):
                result = request_ai_analysis(payload, config, has_data=True, provider_factory=create_configured_provider)
            if result.get("status") == "available" and result.get("scope_validation") == "verified":
                result["selection_fingerprint"] = selection_fingerprint(selection)
                result["ai_metadata"] = dict(provider=config.provider, model=config.model,
                                             generated_at=datetime.now(timezone.utc).isoformat(), status="success")
                st.session_state[key] = result
            else:
                message = status_message(result)
                if result.get("code") == "incomplete_response" and not isinstance(result.get("diagnostics"), dict):
                    message += "\n服务未提供安全诊断，请确认已刷新到最新页面后重试。"
                # Only retain a fixed, sanitized UI message, never the raw result.
                st.session_state[failure_key] = {"identity": failure_identity, "message": message}
        failure = st.session_state.get(failure_key)
        if isinstance(failure, dict):
            st.warning(failure["message"])
        saved = st.session_state.get(key)
        if saved is not None:
            if saved.get("status") != "available" or saved.get("scope_validation") != "verified":
                clear_result(st.session_state, key)
                st.warning("AI 结果尚未完整验证，未展示。")
            elif not render_validated_result(saved, payload, st):
                clear_result(st.session_state, key)
        else:
            st.caption("尚未生成 AI 解读；右侧可离线核查当前计算指标。")
    with right:
        st.subheader("数据与证据")
        st.caption("下列信息为 Python 已计算事实；证据范围匹配不证明自然语言推理正确。")
        with st.expander("分析范围（仅本地）", expanded=False):
            st.json(selection)
        try:
            details = evidence_detail_catalog(payload)
            st.caption(f"当前可引用 {len(details)} 个证据 ID。")
            if details:
                st.caption("离线结果说明：选择证据后查看 Python 已计算的值及口径，无需 API。")
                evidence_id = st.selectbox("核查证据", list(details), key=f"ai_center_evidence_{kind}")
                rows = details[evidence_id]
                st.dataframe([{field: row[field] for field in ("指标", "值", "单位")} for row in rows],
                             hide_index=True, width="stretch")
                st.caption(f"类别：{rows[0]['类别']}；轴向：{rows[0]['轴向']}；批次：{rows[0]['批次范围']}")
                st.caption(f"计算口径：{rows[0]['口径']}")
            else:
                st.info("当前分析没有可展示的证据。")
        except (ValueError, TypeError, KeyError):
            st.warning("当前证据目录无法读取，请返回分析页面重新计算。")
