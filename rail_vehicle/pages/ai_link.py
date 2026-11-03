"""Register the current aggregate analysis and link to the AI center."""

import streamlit as st
from copy import deepcopy
from datetime import date, datetime, time

from rail_vehicle.ai.center_state import CONTEXTS, clear_context, register_context
from rail_vehicle.ai.ui import clear_result
from rail_vehicle.ui import navigate


_WIDGET_PREFIXES = {
    "single": ("dynamics_", "analysis_start_", "analysis_end_", "analysis_threshold"),
    "batch": ("comparison_vehicle_id", "comparison_batch_ids_"),
    "ml": ("ml_source", "ml_vehicle", "ml_reference_", "ml_target_", "ml_contamination", "ml_rule_threshold"),
    "vibration": ("vibration_source", "vibration_vehicle", "vibration_batch_", "vibration_range_", "vibration_band_", "vibration_window"),
    "fleet": ("fleet_source", "fleet_vehicles", "fleet_batch_", "fleet_speed_range"),
}


def _open_center(kind):
    prefixes = _WIDGET_PREFIXES[kind]
    allowed = (str, int, float, tuple, list, date, datetime, time)
    snapshot = {key: deepcopy(value) for key, value in st.session_state.items()
                if key.startswith(prefixes) and isinstance(value, allowed) and not isinstance(value, bool)}
    snapshots = dict(st.session_state.get("ai_center_source_widgets", {}))
    snapshots[kind] = snapshot
    st.session_state["ai_center_source_widgets"] = snapshots
    navigate("AI 智能分析中心", ai_center_requested_kind=kind)


def restore_source_widgets(kind):
    snapshots = st.session_state.get("ai_center_source_widgets", {})
    if isinstance(snapshots, dict) and isinstance(snapshots.get(kind), dict):
        for key, value in snapshots[kind].items():
            st.session_state[key] = value
        remaining = dict(snapshots)
        remaining.pop(kind, None)
        st.session_state["ai_center_source_widgets"] = remaining


def render_center_link(kind, payload, selection, *, guard_key=None, guard_fingerprint=None):
    try:
        register_context(st.session_state, kind, payload, selection,
                         guard_key=guard_key, guard_fingerprint=guard_fingerprint)
    except (ValueError, TypeError, KeyError, OverflowError):
        clear_context(st.session_state, kind)
        clear_result(st.session_state, CONTEXTS[kind][2])
        st.warning("当前结果无法组成 AI 输入；本地统计和报告仍可使用。")
        return None, None
    saved = st.session_state.get(CONTEXTS[kind][2])
    if saved is not None and (not isinstance(saved, dict) or saved.get("status") != "available"
                              or saved.get("scope_validation") != "verified"):
        clear_result(st.session_state, CONTEXTS[kind][2])
        st.warning("AI 结果尚未完整验证，未展示。")
    st.caption("当前聚合结果已准备好，可在 AI 智能分析中心查看证据并主动生成解读。")
    st.button("前往 AI 智能分析中心 →", key=f"ai_center_link_{kind}",
              on_click=_open_center, args=(kind,))
    return payload, st.session_state.get(CONTEXTS[kind][2])
