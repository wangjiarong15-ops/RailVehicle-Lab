"""AI center navigation, session isolation and opt-in dispatch."""

from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rail_vehicle.ai.center_state import current_context, register_context
from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.context import build_single_batch_payload
from rail_vehicle.ai.provider import FakeProvider
from rail_vehicle.ai.schemas import collect_evidence_metadata
from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.pages import ai_center


def payload_fixture():
    rows = [dict(timestamp="2026-10-08T12:00:00+00:00", speed_kmh=20, lateral_accel=.1, vertical_accel=.2),
            dict(timestamp="2026-10-08T12:00:01+00:00", speed_kmh=30, lateral_accel=.2, vertical_accel=.3)]
    return build_single_batch_payload(
        analysis=analyze_run_data(rows, 1), anomalies=detect_anomalies(rows, 1),
        vehicle=dict(vehicle_type="测试", mass_kg=90000, axle_count=8, bogie_count=4, max_speed_kmh=160),
        batch_ref="B1", selected_start=rows[0]["timestamp"], selected_end=rows[-1]["timestamp"])


def center_app():
    import streamlit as st
    from rail_vehicle.ai.center_state import register_context
    from rail_vehicle.pages.ai_center import render
    from test_ai_center import payload_fixture
    if "fixture_initialized" not in st.session_state:
        register_context(st.session_state, "single", payload_fixture(), {"batch": 1})
        st.session_state.fixture_initialized = True
    render()


def navigation_app():
    import streamlit as st
    from rail_vehicle.pages.ai_center import render
    from rail_vehicle.pages.ai_link import render_center_link
    from test_ai_center import payload_fixture
    if st.session_state.get("navigation_page") == "AI 智能分析中心":
        render()
    else:
        render_center_link("single", payload_fixture(), {"vehicle_id": 1, "batch_id": 2})


def test_snapshot_isolated_and_changed_selection_invalidates_result():
    state = {}
    payload = payload_fixture()
    selection = {"batch": 1}
    register_context(state, "single", payload, selection)
    payload["analysis_type"] = "changed"
    selection["batch"] = 2
    assert current_context(state, "single")["selection"]["batch"] == 1
    state["ai_single_batch_result"] = {"input_fingerprint": "stale", "selection_fingerprint": "stale"}
    register_context(state, "single", payload_fixture(), {"batch": 2})
    assert "ai_single_batch_result" not in state
    state["ai_center_contexts"]["single"]["selection"]["batch"] = 3
    assert current_context(state, "single") is None


def test_source_guard_rejects_changed_experiment():
    state = {"source": {"fingerprint": "old"}}
    register_context(state, "single", payload_fixture(), {}, guard_key="source", guard_fingerprint="old")
    assert current_context(state, "single") is not None
    state["source"]["fingerprint"] = "new"
    assert current_context(state, "single") is None


def test_single_and_comparison_contexts_remain_separate():
    from tests.test_ai_ui import comparison_payload
    state = {}
    register_context(state, "single", payload_fixture(), {"vehicle": 1, "batch": 1})
    register_context(state, "batch", comparison_payload(), {"vehicle": 1, "batches": [1, 2]})
    assert current_context(state, "single")["payload"]["analysis_type"] == "single_batch"
    assert current_context(state, "batch")["payload"]["analysis_type"] == "batch_comparison"
    state["ai_center_contexts"]["batch"]["selection"]["batches"] = [2, 3]
    assert current_context(state, "batch") is None
    assert current_context(state, "single") is not None


def test_center_requires_click_and_renders_validated_evidence():
    payload = payload_fixture()
    evidence = next(item for item in collect_evidence_metadata(payload) if item["evidence_id"] == "metric.speed.mean")
    response = dict(summary=dict(text="速度统计已计算。", evidence_ids=[evidence["evidence_id"]],
                                 claim_scope={key: evidence[key] for key in ("category", "axis", "batch_refs")}),
                    observations=[], possible_causes=[], further_checks=[], limitations=[])
    config = AIConfig(enabled=True, provider="deepseek", model="mock", api_key="placeholder")
    with patch.object(ai_center.AIConfig, "from_sources", return_value=config), \
         patch.object(ai_center, "create_configured_provider", return_value=FakeProvider(response)) as factory:
        app = AppTest.from_function(center_app).run()
        assert not app.exception
        factory.assert_not_called()
        app.button(key="ai_center_generate").click().run()
        assert not app.exception
        factory.assert_called_once()
        assert app.session_state["ai_single_batch_result"]["scope_validation"] == "verified"
        assert any("metric.speed.mean" in item.value for item in app.caption)


def test_public_center_does_not_load_configuration():
    with patch.object(ai_center, "public_demo_enabled", return_value=True), \
         patch.object(ai_center.AIConfig, "from_sources", side_effect=AssertionError("must not read")):
        app = AppTest.from_function(center_app).run()
        assert not app.exception
        assert any("公开演示模式" in item.value for item in app.info)


def test_configuration_exception_is_safe():
    with patch.object(ai_center.AIConfig, "from_sources", side_effect=RuntimeError("private detail")):
        app = AppTest.from_function(center_app).run()
        assert not app.exception
        assert all("private detail" not in item.value for item in app.warning)


def test_offline_evidence_inspection_uses_existing_facts_without_provider():
    with patch.object(ai_center.AIConfig, "from_sources", return_value=AIConfig(enabled=False)), \
         patch.object(ai_center, "create_configured_provider") as factory:
        app = AppTest.from_function(center_app).run()
        app.selectbox(key="ai_center_evidence_single").set_value("metric.speed.mean").run()
        assert not app.exception
        assert any("计算口径" in item.value for item in app.caption)
        assert len(app.dataframe) == 1
        assert "km/h" in str(app.dataframe[0].value)
        factory.assert_not_called()


def test_source_link_opens_center_without_calling_provider():
    with patch.object(ai_center.AIConfig, "from_sources", return_value=AIConfig(enabled=False)), \
         patch.object(ai_center, "create_configured_provider") as factory:
        app = AppTest.from_function(navigation_app).run()
        app.button(key="ai_center_link_single").click().run()
        assert not app.exception
        assert app.session_state["navigation_page"] == "AI 智能分析中心"
        assert app.button(key="ai_center_generate").disabled
        factory.assert_not_called()


def test_invalid_evidence_is_not_displayed_or_cached():
    output = dict(summary=dict(text="PRIVATE_OUTPUT", evidence_ids=["unknown"],
                               claim_scope=dict(category="metric", axis="not_applicable", batch_refs=[])),
                  observations=[], possible_causes=[], further_checks=[], limitations=[])
    config = AIConfig(enabled=True, provider="deepseek", model="mock", api_key="placeholder")
    with patch.object(ai_center.AIConfig, "from_sources", return_value=config), \
         patch.object(ai_center, "create_configured_provider", return_value=FakeProvider(output)):
        app = AppTest.from_function(center_app).run()
        app.button(key="ai_center_generate").click().run()
        assert not app.exception
        assert "ai_single_batch_result" not in app.session_state
        assert any("未知证据" in item.value for item in app.warning)
        assert all("PRIVATE_OUTPUT" not in item.value for item in app.text)
