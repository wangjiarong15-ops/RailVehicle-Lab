"""Navigation between local vehicle, CSV import and single-batch analysis pages."""

import streamlit as st

from rail_vehicle.ui import AI, ANALYSIS, ANALYSIS_MODE, BOGIE, DEMO_DATA, navigate


ANALYSIS_TARGET = "analysis_batch_target"
ANALYSIS_REPLAY_SCOPE = "analysis_replay_scope"
REPORT_COMPARISON = "analysis_report_comparison"
IMPORT_VEHICLE = "import_vehicle_target"
WORKBENCH_SOURCE = "workbench_analysis_source"
WORKBENCH_RETURN = "workbench_analysis_return"
DEMO_EVIDENCE_SCOPE = "workbench_demo_evidence_scope"
EVIDENCE_RETURN = "workbench_evidence_return"
_WORKBENCH_WIDGET_PREFIXES = (
    "dynamics_", "demo_dynamics_", "analysis_start_", "analysis_end_", "anomaly_review_event",
    "anomaly_review_seconds", "history_sampling_", "demo_sampling_",
)


def remember_workbench_selection():
    """Keep UI inputs across a visit to the existing 3D or evidence view."""
    route = st.session_state.get("navigation_page")
    if route not in (ANALYSIS, DEMO_DATA):
        return
    inputs = {key: value for key, value in st.session_state.items()
              if key.startswith(_WORKBENCH_WIDGET_PREFIXES)}
    for key in (WORKBENCH_SOURCE, "analysis_threshold", "demo_analysis_threshold", ANALYSIS_REPLAY_SCOPE):
        if key in st.session_state:
            inputs[key] = st.session_state[key]
    st.session_state[WORKBENCH_RETURN] = (route, inputs)


def return_to_workbench(comparison=None):
    route, inputs = st.session_state.pop(WORKBENCH_RETURN)
    st.session_state.update(inputs)
    if comparison is not None:
        st.session_state[REPORT_COMPARISON] = comparison
    navigate(route, **{ANALYSIS_MODE: "single"})


def remember_evidence_selection(*, fictional):
    """Keep one evidence-to-replay return step within the originating analysis."""
    if (WORKBENCH_RETURN not in st.session_state
            or fictional != (DEMO_EVIDENCE_SCOPE in st.session_state)):
        return
    keys = ("ai_demo_evidence",) if fictional else (
        "ai_center_evidence_single", "ai_center_cited_event", "ai_center_timeout",
    )
    settings = {key: st.session_state[key] for key in keys if key in st.session_state}
    settings.update(ai_center_demo=fictional, ai_center_show_history=False)
    if not fictional:
        settings.update((key, value) for key, value in st.session_state.items()
                        if key.startswith("ai_center_model_"))
        settings["ai_center_requested_kind"] = "single"
    st.session_state[EVIDENCE_RETURN] = settings


def return_to_evidence():
    navigate(AI, **st.session_state.pop(EVIDENCE_RETURN))


def clear_workbench_navigation(page):
    if page not in (AI, BOGIE):
        st.session_state.pop(WORKBENCH_RETURN, None)
    if page != BOGIE:
        st.session_state.pop(EVIDENCE_RETURN, None)
    if page != AI and EVIDENCE_RETURN not in st.session_state:
        st.session_state.pop(DEMO_EVIDENCE_SCOPE, None)


def render_workbench_return(key):
    if WORKBENCH_RETURN in st.session_state:
        st.button("返回原分析范围", key=key, icon=":material/arrow_back:", on_click=return_to_workbench)


def render_replay_return():
    if EVIDENCE_RETURN in st.session_state:
        st.button("返回证据核查", key="bogie_return_evidence", icon=":material/arrow_back:",
                  on_click=return_to_evidence)
    render_workbench_return("bogie_return_analysis")


def open_analysis(vehicle_id, batch_id=None, *, threshold=None, scope=None):
    settings = {ANALYSIS_TARGET: {"vehicle_id": vehicle_id, "batch_id": batch_id},
                "dynamics_vehicle_id": vehicle_id, WORKBENCH_SOURCE: "history", ANALYSIS_MODE: "single"}
    if batch_id is not None:
        settings["dynamics_batch_id"] = batch_id
    if threshold is not None:
        settings[ANALYSIS_TARGET]["threshold"] = threshold
    if scope is not None:
        settings[ANALYSIS_TARGET]["scope"] = scope
    navigate("运行分析", **settings)


def analysis_button(container, *, label, key, vehicle_id, batch_id=None, threshold=None, scope=None, primary=False):
    container.button(label, key=key, width="stretch", type="primary" if primary else "secondary",
                     on_click=open_analysis, args=(vehicle_id, batch_id),
                     kwargs=dict(threshold=threshold, scope=scope))


def import_button(container, *, label, key, vehicle_id=None, primary=False):
    container.button(label, key=key, width="stretch", type="primary" if primary else "secondary", on_click=navigate,
                     args=("CSV 数据导入",), kwargs={IMPORT_VEHICLE: vehicle_id})
