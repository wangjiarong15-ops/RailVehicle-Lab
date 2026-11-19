"""Navigation between local vehicle, CSV import and single-batch analysis pages."""

import streamlit as st

from rail_vehicle.ui import AI, ANALYSIS, ANOMALIES, ANALYSIS_MODE, BATCH_VIEWS, BOGIE, COMPARISON, DEMO_DATA, REPORT, analysis_inputs, navigate


ANALYSIS_TARGET = "analysis_batch_target"
ANALYSIS_REPLAY_SCOPE = "analysis_replay_scope"
REPORT_COMPARISON = "analysis_report_comparison"
IMPORT_VEHICLE = "import_vehicle_target"
WORKBENCH_SOURCE = "workbench_analysis_source"
WORKBENCH_RETURN = "workbench_analysis_return"
DEMO_EVIDENCE_SCOPE = "workbench_demo_evidence_scope"
EVIDENCE_RETURN = "workbench_evidence_return"
BATCH_HANDOFF = "workbench_batch_handoff"
AI_REPLAY_SCOPE = "workbench_ai_replay_scope"


def evidence_replay_scope(selection):
    scoped = st.session_state.get(AI_REPLAY_SCOPE)
    return scoped[1] if scoped and scoped[0] == selection else None


def remember_workbench_selection():
    """Keep UI inputs across a visit to the existing 3D or evidence view."""
    route = st.session_state.get("navigation_page")
    if route not in BATCH_VIEWS:
        return
    st.session_state[WORKBENCH_RETURN] = (route, analysis_inputs())


def return_to_workbench(comparison=None, *, prepare_report=False):
    route, inputs = st.session_state.pop(WORKBENCH_RETURN)
    if comparison is not None:
        inputs[REPORT_COMPARISON] = comparison
        if comparison.fictional:
            inputs[WORKBENCH_SOURCE] = "demo"
    if prepare_report and route == DEMO_DATA:
        inputs[WORKBENCH_SOURCE] = "demo"
    if comparison is not None or prepare_report:
        route = REPORT
    navigate(route, **{**inputs, ANALYSIS_MODE: "single"})


def select_workspace_page(page):
    """Carry only the result actually computed on the active batch view."""
    current = st.session_state.get("navigation_page")
    if current == page:
        return
    handoff = st.session_state.get(BATCH_HANDOFF) if current in BATCH_VIEWS else None
    if handoff and page == AI:
        # Local imports keep the link helpers reusable without an import cycle.
        from rail_vehicle.pages.ai_link import _open_center
        from rail_vehicle.pages.run_analysis import _open_demo_evidence
        if handoff["target"]["fictional"]:
            _open_demo_evidence(*handoff["demo_evidence"])
        else:
            _open_center("single", handoff["evidence_id"], handoff["evidence_selection"], handoff["target"].get("scope"))
        return
    if handoff and page == BOGIE:
        remember_workbench_selection()
        from rail_vehicle.pages.bogie_link import _open_replay
        _open_replay("bogie_batch_target", handoff["target"], False)
        return
    if current == COMPARISON and page == AI:
        from rail_vehicle.ai.center_state import current_context
        from rail_vehicle.pages.ai_link import _open_center
        if current_context(st.session_state, "batch") is not None:
            _open_center("batch")
            return
    settings = {ANALYSIS_MODE: "single"}
    if current == DEMO_DATA and page in BATCH_VIEWS:
        settings[WORKBENCH_SOURCE] = "demo"
    if handoff and not handoff["target"]["fictional"]:
        vehicle_id = handoff["target"]["vehicle_id"]
        if page == COMPARISON:
            settings["comparison_vehicle_id"] = vehicle_id
        elif page == "车辆参数":
            settings["vehicle_focus_target"] = vehicle_id
        elif page == "CSV 数据导入":
            settings[IMPORT_VEHICLE] = vehicle_id
    navigate(page, **settings)


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
        st.session_state.pop(AI_REPLAY_SCOPE, None)
    if page != BOGIE:
        st.session_state.pop(EVIDENCE_RETURN, None)
    if page != AI and EVIDENCE_RETURN not in st.session_state:
        st.session_state.pop(DEMO_EVIDENCE_SCOPE, None)


def render_workbench_return(key):
    if WORKBENCH_RETURN in st.session_state:
        st.button("返回原分析范围", key=key, icon=":material/arrow_back:", on_click=return_to_workbench)


def render_original_report_link(key):
    origin = st.session_state.get(WORKBENCH_RETURN)
    if origin is not None and origin[0] in BATCH_VIEWS and origin[0] != REPORT:
        st.button("按原分析范围准备报告", key=key, icon=":material/description:",
                  help="沿用进入当前页面前的批次、范围和异常阈值。",
                  on_click=return_to_workbench, kwargs={"prepare_report": True})


def render_replay_return():
    if EVIDENCE_RETURN in st.session_state:
        st.button("返回证据核查", key="bogie_return_evidence", icon=":material/arrow_back:",
                  on_click=return_to_evidence)
    origin = st.session_state.get(WORKBENCH_RETURN)
    if origin is None:
        return
    with st.container(horizontal=True, vertical_alignment="center"):
        render_workbench_return("bogie_return_analysis")
        if EVIDENCE_RETURN not in st.session_state:
            render_original_report_link("bogie_original_report")


def open_analysis(vehicle_id, batch_id=None, *, threshold=None, scope=None, page=ANALYSIS):
    settings = {ANALYSIS_TARGET: {"vehicle_id": vehicle_id, "batch_id": batch_id},
                "dynamics_vehicle_id": vehicle_id, WORKBENCH_SOURCE: "history", ANALYSIS_MODE: "single"}
    if batch_id is not None:
        settings["dynamics_batch_id"] = batch_id
    if threshold is not None:
        settings[ANALYSIS_TARGET]["threshold"] = threshold
    if scope is not None:
        settings[ANALYSIS_TARGET]["scope"] = scope
    navigate(page, **settings)


def analysis_button(container, *, label, key, vehicle_id, batch_id=None, threshold=None, scope=None, primary=False,
                    page=ANALYSIS):
    container.button(label, key=key, width="stretch", type="primary" if primary else "secondary",
                     on_click=open_analysis, args=(vehicle_id, batch_id),
                     kwargs=dict(threshold=threshold, scope=scope, page=page))


def import_button(container, *, label, key, vehicle_id=None, primary=False):
    container.button(label, key=key, width="stretch", type="primary" if primary else "secondary", on_click=navigate,
                     args=("CSV 数据导入",), kwargs={IMPORT_VEHICLE: vehicle_id})
