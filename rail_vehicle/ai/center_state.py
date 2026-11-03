"""Session-only, validated AI contexts shared by analysis pages and the center."""

from copy import deepcopy

from rail_vehicle.ai.provider import input_fingerprint
from rail_vehicle.ai.schemas import validate_input_payload
from rail_vehicle.ai.ui import invalidate_stale_result, selection_fingerprint


CONTEXTS = {
    "single": ("单批次运行分析", "single_batch", "ai_single_batch_result"),
    "batch": ("同车多批次对比", "batch_comparison", "ai_batch_comparison_result"),
    "ml": ("机器学习实验", "ml_experiment", "experiment_ml_ai"),
    "vibration": ("振动分析", "vibration_experiment", "experiment_vibration_ai"),
    "fleet": ("多车辆对比", "fleet_comparison", "experiment_fleet_ai"),
}
STATE_KEY = "ai_center_contexts"


def clear_context(state, kind):
    contexts = dict(state.get(STATE_KEY, {}))
    contexts.pop(kind, None)
    state[STATE_KEY] = contexts


def clear_all_contexts(state):
    """Invalidate snapshots and AI results after a vehicle record is edited."""
    state[STATE_KEY] = {}
    state.pop("ai_center_source_widgets", None)
    for _, _, result_key in CONTEXTS.values():
        state.pop(result_key, None)


def register_context(state, kind, payload, selection, *, guard_key=None, guard_fingerprint=None):
    """Only validated aggregate payloads enter the session registry, never raw samples."""
    if kind not in CONTEXTS or payload.get("analysis_type") != CONTEXTS[kind][1]:
        raise ValueError("分析类型不匹配。")
    validate_input_payload(payload)
    key = CONTEXTS[kind][2]
    invalidate_stale_result(state, key, payload, selection)
    context = dict(payload=deepcopy(payload), selection=deepcopy(selection),
                   input_fingerprint=input_fingerprint(payload),
                   selection_fingerprint=selection_fingerprint(selection),
                   guard_key=guard_key, guard_fingerprint=guard_fingerprint)
    contexts = dict(state.get(STATE_KEY, {}))
    contexts[kind] = context
    state[STATE_KEY] = contexts


def current_context(state, kind):
    """Reject changed snapshots or invalidated source experiments."""
    try:
        item = state.get(STATE_KEY, {}).get(kind)
        if not isinstance(item, dict) or kind not in CONTEXTS:
            return None
        payload, selection = item["payload"], item["selection"]
        validate_input_payload(payload)
        if (payload.get("analysis_type") != CONTEXTS[kind][1]
                or item["input_fingerprint"] != input_fingerprint(payload)
                or item["selection_fingerprint"] != selection_fingerprint(selection)):
            return None
        guard = item.get("guard_key")
        if guard and state.get(guard, {}).get("fingerprint") != item.get("guard_fingerprint"):
            return None
        return item
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None
