"""Prepare experiment facts for the shared, opt-in AI center."""

import streamlit as st

from rail_vehicle.ai.center_state import clear_context
from rail_vehicle.ai.experiment_context import build_ml_experiment_payload, build_vibration_experiment_payload
from rail_vehicle.ai.fleet_context import build_fleet_comparison_payload
from rail_vehicle.ai.ui import clear_result
from rail_vehicle.deployment import public_demo_enabled
from rail_vehicle.pages.ai_link import render_center_link


GUARD_KEYS = {"ml": "ml_detection_result", "vibration": "vibration_analysis_result", "fleet": "fleet_comparison_result"}


def render_experiment_ai(saved, fingerprint, selection, kind):
    """Register validated aggregate facts; return current AI output for PDF only."""
    key = f"experiment_{kind}_ai"
    if public_demo_enabled():
        clear_context(st.session_state, kind)
        clear_result(st.session_state, key)
        st.info("公开演示模式不调用 AI 服务；本地实验分析与 PDF 导出仍可使用。")
        return None, None
    if not saved or saved.get("fingerprint") != fingerprint:
        clear_context(st.session_state, kind)
        clear_result(st.session_state, key)
        return None, None
    try:
        builder = {"ml": build_ml_experiment_payload, "vibration": build_vibration_experiment_payload, "fleet": build_fleet_comparison_payload}[kind]
        payload = builder(saved["result"])
        local_selection = dict(selection=selection, analysis_fingerprint=fingerprint)
        return render_center_link(kind, payload, local_selection, guard_key=GUARD_KEYS[kind], guard_fingerprint=fingerprint)
    except (ValueError, TypeError, KeyError, OverflowError):
        clear_context(st.session_state, kind)
        clear_result(st.session_state, key)
        st.warning("当前结果不符合 AI 输入要求；本地分析与报告仍可使用。")
        return None, None
