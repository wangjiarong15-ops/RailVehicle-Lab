"""AI center navigation, session isolation and opt-in dispatch."""

from unittest.mock import patch
import pytest

from streamlit.testing.v1 import AppTest

from rail_vehicle.ai.center_state import current_context, register_context
from rail_vehicle.ai.config import AIConfig
from rail_vehicle.ai.context import build_single_batch_payload
from rail_vehicle.ai.provider import AIProviderUnavailable, FakeProvider
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


def actual_run_source_app():
    import streamlit as st
    from rail_vehicle.pages import ai_center, run_analysis
    if st.session_state.get("navigation_page") == "AI 智能分析中心":
        ai_center.render()
    elif st.session_state.get("test_kind") == "batch":
        run_analysis._render_batch_comparison()
    else:
        run_analysis._render_dynamics_analysis()


@pytest.mark.parametrize("kind,result_key", [("single", "ai_single_batch_result"),
                                               ("batch", "ai_batch_comparison_result")])
def test_actual_run_analysis_round_trip_preserves_selection_and_invalidates_changed_input(kind, result_key):
    from datetime import time
    from rail_vehicle.demo_data import analysis_demo_data
    from rail_vehicle.pages import run_analysis
    from tests.test_ai_ui import valid_response
    vehicle, batches, samples = analysis_demo_data()
    class LocalFake:
        def analyze(self, payload):
            return FakeProvider(valid_response(payload)).analyze(payload)
    config = AIConfig(enabled=True, provider="deepseek", model="mock", api_key="placeholder")
    with patch.object(run_analysis, "list_vehicles", return_value=[vehicle]), \
         patch.object(run_analysis, "list_vehicle_batches", return_value=batches), \
         patch.object(run_analysis, "list_batch_samples", side_effect=lambda _vehicle, batch: samples[batch]), \
         patch.object(ai_center.AIConfig, "from_sources", return_value=config), \
         patch.object(ai_center, "create_configured_provider", return_value=LocalFake()) as factory:
        app = AppTest.from_function(actual_run_source_app, default_timeout=20)
        app.session_state["test_kind"] = kind
        app.run()
        assert not app.exception
        if kind == "single":
            app.number_input(key="analysis_threshold").set_value(1.5).run()
        else:
            assert app.multiselect(key="comparison_batch_ids_-1").value == [-1, -2]
        app.button(key=f"ai_center_link_{kind}").click().run()
        factory.assert_not_called()
        app.button(key="ai_center_generate").click().run()
        assert not app.exception
        assert app.session_state[result_key]["scope_validation"] == "verified"
        next(button for button in app.button if button.label == "返回分析页面").click().run()
        assert not app.exception
        assert app.session_state["navigation_page"] == "运行分析"
        assert result_key in app.session_state
        if kind == "single":
            assert app.number_input(key="analysis_threshold").value == 1.5
            app.time_input(key="analysis_start_time_-1_-1").set_value(time(9, 30, 10)).run()
        else:
            assert app.multiselect(key="comparison_batch_ids_-1").value == [-1, -2]
            app.multiselect(key="comparison_batch_ids_-1").set_value([-1]).run()
        assert result_key not in app.session_state
        factory.assert_called_once()


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


def all_fictional_payloads():
    pytest.importorskip("sklearn")
    pytest.importorskip("scipy")
    from tests.test_ai_ui import comparison_payload
    from rail_vehicle.ai.experiment_context import build_ml_experiment_payload, build_vibration_experiment_payload
    from rail_vehicle.ai.fleet_context import build_fleet_comparison_payload
    from rail_vehicle.demo_data import ml_demo_samples, vibration_demo_samples
    from rail_vehicle.ml_anomaly import detect_ml_anomalies
    from rail_vehicle.vibration import analyze_vibration
    from rail_vehicle.fleet_comparison import compare_vehicles
    from rail_vehicle.pages.fleet_comparison import demo_entries
    return {
        "single": payload_fixture(),
        "batch": comparison_payload(),
        "ml": build_ml_experiment_payload(detect_ml_anomalies(*ml_demo_samples())),
        "vibration": build_vibration_experiment_payload(analyze_vibration(vibration_demo_samples())),
        "fleet": build_fleet_comparison_payload(dict(comparison=compare_vehicles(demo_entries()), speed_min=0, speed_max=600)),
    }


def test_all_five_contexts_accept_only_their_own_aggregate_types():
    payloads = all_fictional_payloads()
    state = {}
    for kind, payload in payloads.items():
        register_context(state, kind, payload, {"batch": kind})
        assert current_context(state, kind)["payload"]["analysis_type"] == payload["analysis_type"]
    for kind in payloads:
        other = payloads["batch" if kind == "single" else "single"]
        with pytest.raises(ValueError):
            register_context(state, kind, other, {})


def test_each_center_context_returns_to_its_source_without_calling_provider():
    targets = {"single": "运行分析", "batch": "运行分析", "ml": "机器学习异常检测",
               "vibration": "振动与平稳性分析", "fleet": "多车辆运行对比"}
    state = {}
    for kind, payload in all_fictional_payloads().items():
        register_context(state, kind, payload, {"batch": kind})
    with patch.object(ai_center.AIConfig, "from_sources", return_value=AIConfig(enabled=False)), \
         patch.object(ai_center, "create_configured_provider") as factory:
        app = AppTest.from_function(center_app, default_timeout=15)
        app.session_state["fixture_initialized"] = True
        app.session_state["ai_center_contexts"] = state["ai_center_contexts"]
        for kind, target in targets.items():
            app.session_state["ai_center_requested_kind"] = kind
            app.run()
            assert not app.exception
            next(button for button in app.button if button.label == "返回分析页面").click().run()
            assert app.session_state["navigation_page"] == target
            factory.assert_not_called()


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


def test_model_change_invalidates_success_without_automatic_request():
    from tests.test_ai_ui import valid_response
    response = valid_response(payload_fixture())
    config = AIConfig(enabled=True, provider="deepseek", model="model-one", api_key="placeholder")
    with patch.object(ai_center.AIConfig, "from_sources", return_value=config), \
         patch.object(ai_center, "create_configured_provider", return_value=FakeProvider(response)) as factory:
        app = AppTest.from_function(center_app).run()
        app.button(key="ai_center_generate").click().run()
        assert app.session_state["ai_single_batch_result"]["scope_validation"] == "verified"
        app.text_input(key="ai_center_model_deepseek").set_value("model-two").run()
        assert not app.exception
        assert "ai_single_batch_result" not in app.session_state
        assert all("速度统计结果已计算" not in item.value for item in app.text)
        factory.assert_called_once()


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


def test_model_configuration_change_refreshes_stale_widget_but_manual_edit_survives_rerun():
    first = AIConfig(enabled=True, provider="deepseek", model="model-one", api_key="placeholder")
    second = AIConfig(enabled=True, provider="deepseek", model="model-two", api_key="placeholder")
    with patch.object(ai_center.AIConfig, "from_sources", return_value=first) as load:
        app = AppTest.from_function(center_app).run()
        app.text_input(key="ai_center_model_deepseek").set_value("manual-model").run()
        assert app.text_input(key="ai_center_model_deepseek").value == "manual-model"
        load.return_value = second
        app.run()
        assert not app.exception
        assert app.text_input(key="ai_center_model_deepseek").value == "model-two"


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
        next(button for button in app.button if button.label == "返回分析页面").click().run()
        assert not app.exception
        assert app.session_state["navigation_page"] == "运行分析"
        assert app.button(key="ai_center_link_single")
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


def test_incomplete_response_diagnostics_reach_center_without_partial_output():
    class IncompleteProvider:
        def analyze(self, payload):
            raise AIProviderUnavailable("incomplete_response", "incomplete", diagnostics={
                "status": "incomplete", "incomplete_reason": "max_output_tokens",
                "has_message": True, "has_output_text": True, "output_text_length": 42,
                "has_refusal": False, "max_output_tokens_limit": 3000, "usage_output_tokens": 3000,
                "reasoning_tokens": 0, "untrusted_body": "PRIVATE_PARTIAL_OUTPUT"})
    config = AIConfig(enabled=True, provider="deepseek", model="mock", api_key="placeholder")
    with patch.object(ai_center.AIConfig, "from_sources", return_value=config), \
         patch.object(ai_center, "create_configured_provider", return_value=IncompleteProvider()):
        app = AppTest.from_function(center_app).run()
        app.button(key="ai_center_generate").click().run()
        assert not app.exception
        warnings = " ".join(item.value for item in app.warning)
        assert "incomplete_details.reason=max_output_tokens" in warnings
        assert "PRIVATE_PARTIAL_OUTPUT" not in warnings
        assert "ai_single_batch_result" not in app.session_state
        app.selectbox(key="ai_center_evidence_single").set_value("metric.speed.mean").run()
        assert any("incomplete_details.reason=max_output_tokens" in item.value for item in app.warning)
        app.text_input(key="ai_center_model_deepseek").set_value("another-model").run()
        assert not any("incomplete_details.reason=max_output_tokens" in item.value for item in app.warning)
        assert "ai_center_last_failure" not in app.session_state


def test_timeout_change_clears_previous_failure_without_retrying():
    class TimeoutProvider:
        def analyze(self, payload):
            raise AIProviderUnavailable("timeout", "timeout")

    config = AIConfig(enabled=True, provider="deepseek", model="mock", api_key="placeholder", timeout=30)
    with patch.object(ai_center.AIConfig, "from_sources", return_value=config), \
         patch.object(ai_center, "create_configured_provider", return_value=TimeoutProvider()) as factory:
        app = AppTest.from_function(center_app).run()
        app.button(key="ai_center_generate").click().run()
        assert any("请求超时" in item.value for item in app.warning)
        assert "ai_center_last_failure" in app.session_state
        app.number_input(key="ai_center_timeout").set_value(60).run()
        assert not app.exception
        assert "ai_center_last_failure" not in app.session_state
        assert not any("请求超时" in item.value for item in app.warning)
        assert "ai_single_batch_result" not in app.session_state
        factory.assert_called_once()
