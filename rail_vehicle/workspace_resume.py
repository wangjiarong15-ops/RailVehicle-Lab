"""Resume a verified single-batch selection within the same browser tab."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re

import streamlit as st

from rail_vehicle import db
from rail_vehicle.pages.data_workflow import (
    BATCH_HANDOFF, WORKBENCH_RETURN, WORKBENCH_SOURCE, select_workspace_page,
)
from rail_vehicle.replay_evidence import ReplayEvidenceScope
from rail_vehicle.ui import AI, ANOMALIES, BATCH_VIEWS, BOGIE, DEMO_DATA, LINKED, RESUME_PARAM

INPUT = "workspace_resume_input"
EVENT = "workspace_resume_event"
BOGIE_EVENT = "workspace_resume_bogie_event"
_CURRENT = "workspace_resume_checkpoint"
_FORWARD = "workspace_resume_destination"
_NOTICE = "workspace_resume_notice"
_STALE_NOTICE_PAGE = "workspace_resume_stale_notice_page"
_READY = "workspace_resume_ready"
_STALE_NOTICE = "上次的批次、范围或事件已变化，未继续打开旧证据。请核对当前筛选。"
_BROWSER_JS = """
export default function ({data, setStateValue}) {
  const {storageKey, loading, snapshot, queryKey} = data;
  if (loading) {
    let payload = '';
    try { payload = sessionStorage.getItem(storageKey) || ''; } catch (_) {}
    setStateValue('snapshot', payload.length > 2048 ? '' : payload);
    return;
  }
  try {
    const url = new URL(location.href);
    if (snapshot === null) {
      sessionStorage.removeItem(storageKey);
      url.searchParams.delete(queryKey);
    } else {
      sessionStorage.setItem(storageKey, JSON.stringify(snapshot));
      url.searchParams.set(queryKey, '1');
      // A rail_page from a theme link may describe the previous page.
      url.searchParams.delete('rail_page');
    }
    if (url.href !== location.href) history.replaceState(history.state, '', url.href);
  } catch (_) { /* Blocked browser storage must not affect analysis. */ }
}
"""


def _integer(value):
    return type(value) is int and abs(value) <= 2**63 - 1


def _utc(value):
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError("Invalid timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("Expected a UTC timestamp")
    return stamp.astimezone(timezone.utc)


def _scope(values):
    if not isinstance(values, list) or len(values) != 5:
        raise ValueError("Invalid replay window")
    first, last, count, start, end = values
    if (not all(_integer(v) for v in values[:3]) or not 0 <= first <= last < count
            or _utc(start) > _utc(end)):
        raise ValueError("Invalid replay window")
    return ReplayEvidenceScope(first, last, count, _utc(start), _utc(end))


def decode_checkpoint(raw, *, public):
    """Treat browser state as untrusted input, accepting only known selectors."""
    if not isinstance(raw, str) or not 0 < len(raw) <= 2048:
        return None
    try:
        saved = json.loads(raw)
        if not isinstance(saved, dict) or type(saved.get("v")) is not int or saved["v"] != 1:
            return None
        origin, page, demo = saved["origin"], saved["page"], saved["demo"]
        if (origin not in BATCH_VIEWS or page not in (*BATCH_VIEWS, AI, BOGIE, LINKED)
                or (page in BATCH_VIEWS and origin != page) or type(demo) is not bool
                or ((public or origin == DEMO_DATA) and not demo)):
            return None
        vehicle, batch = saved["vehicle"], saved["batch"]
        if not _integer(vehicle) or not _integer(batch) or not vehicle or not batch:
            return None
        start, end = _utc(saved["start"]), _utc(saved["end"])
        threshold = saved["threshold"]
        if start > end or type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold < 0:
            return None
        clean = {k: saved[k] for k in ("v", "origin", "page", "demo", "vehicle", "batch", "start", "end", "threshold")}
        if "rows" in saved:
            scope = _scope(saved["rows"])
            if demo or (scope.first_timestamp, scope.last_timestamp) != (start, end):
                return None
            clean["rows"] = saved["rows"]
        if "event" in saved:
            if not isinstance(saved["event"], list) or len(saved["event"]) != 2:
                return None
            idx, identity = saved["event"]
            if (not _integer(idx) or idx < 0 or not isinstance(identity, str)
                    or re.fullmatch(r"[0-9a-f]{64}", identity) is None):
                return None
            clean["event"] = [idx, identity]
        if page in (BOGIE, LINKED) and saved.get("event_jump") is True:
            if "event" not in clean:
                return None
            clean["event_jump"] = True
        return clean
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return None


def _mount(slot, *, public, loading=False, snapshot=None):
    # Separate databases and public-demo mode even if they reuse a localhost port.
    workbench = "public" if public else sha256(str(db.DB_PATH.resolve()).encode()).hexdigest()[:24]
    # Streamlit gives each AppTest runtime its own component registry.
    browser = st.components.v2.component("rail_workspace_resume", html="<span hidden></span>", js=_BROWSER_JS)
    with slot.container(gap=None):
        return browser(
            key="rail_workspace_restore" if loading else "rail_workspace_store",
            height=1, on_snapshot_change=lambda: None,
            data={"storageKey": f"rail_workspace_v1:{workbench}", "loading": loading,
                  "snapshot": snapshot, "queryKey": RESUME_PARAM},
        )


def _skip_restore():
    st.session_state[_READY] = True


def restore_workspace(slot, *, public):
    if st.session_state.get(_READY):
        return
    if st.query_params.get(RESUME_PARAM) != "1":
        st.session_state[_READY] = True
        return
    result = _mount(slot, public=public, loading=True)
    if result.snapshot is None:
        st.caption("正在恢复此标签页的分析筛选…")
        st.button("跳过恢复", key="workspace_resume_skip", on_click=_skip_restore)
        st.stop()
    st.session_state[_READY] = True
    snapshot = decode_checkpoint(result.snapshot, public=public)
    if snapshot is None:
        return
    st.session_state["navigation_page"] = snapshot["origin"]
    st.session_state[WORKBENCH_SOURCE] = "demo" if snapshot["demo"] else "history"
    if public or snapshot["origin"] == DEMO_DATA:
        st.session_state["statistics_demo_started"] = True
    st.session_state[INPUT] = {
        "demo": snapshot["demo"], "vehicle": snapshot["vehicle"], "batch": snapshot["batch"],
        "start": _utc(snapshot["start"]), "end": _utc(snapshot["end"]), "threshold": snapshot["threshold"],
        "scope": _scope(snapshot["rows"]) if "rows" in snapshot else None,
    }
    st.session_state[EVENT] = snapshot.get("event")
    st.session_state[_FORWARD] = snapshot


def prior_event():
    checkpoint = st.session_state.get(_CURRENT)
    return checkpoint.get("event") if checkpoint else None


def _capture(page, previous):
    handoff = st.session_state.get(BATCH_HANDOFF)
    if page in BATCH_VIEWS and handoff:
        selection = handoff["evidence_selection"]
        target = handoff["target"]
        snapshot = {"v": 1, "page": page, "origin": page, "demo": target["fictional"],
                    "vehicle": target["vehicle_id"], "batch": target["batch_id"],
                    "start": selection["selected_start"], "end": selection["selected_end"],
                    "threshold": target["threshold"]}
        if "row_span" in selection and (scope := target.get("scope")):
            snapshot["rows"] = [scope.first_row, scope.last_row, scope.batch_count,
                                scope.first_timestamp.isoformat(), scope.last_timestamp.isoformat()]
        same_context = previous and all(previous.get(k) == snapshot.get(k) for k in (
            "demo", "vehicle", "batch", "start", "end", "threshold", "rows"))
        fingerprint = st.session_state.get("anomaly_review_fingerprint")
        if page == ANOMALIES and handoff["evidence_id"] is not None and fingerprint:
            snapshot["event"] = [st.session_state["anomaly_review_event"], fingerprint]
        elif page != ANOMALIES and same_context and "event" in previous:
            snapshot["event"] = previous["event"]
        return snapshot
    if page in (AI, BOGIE, LINKED) and previous and (origin := st.session_state.get(WORKBENCH_RETURN)):
        if origin[0] != previous["origin"]:
            return None
        snapshot = {**previous, "page": page}
        snapshot.pop("event_jump", None)
        if page in (BOGIE, LINKED):
            from rail_vehicle.pages.bogie import DEMO, HISTORY
            expected = ((DEMO, previous["batch"]) if previous["demo"] else
                        (HISTORY, previous["vehicle"], previous["batch"]))
            threshold = st.session_state.get("bogie_threshold")
            if (expected != st.session_state.get("bogie_selection") or st.session_state.get("bogie_track") is None
                    or previous["threshold"] != (1.0 if threshold is None else threshold)):
                return None
            if jump := st.session_state.get(BOGIE_EVENT):
                if "event" not in snapshot or not handoff or jump != handoff["event_target"]:
                    return {**snapshot, "page": snapshot["origin"]}
                snapshot["event_jump"] = True
        else:
            # A manual change in the AI center is independent of the source event.
            evidence_key = "ai_demo_evidence" if previous["demo"] else "ai_center_evidence_single"
            if (st.session_state.get("ai_center_show_history")
                    or st.session_state.get("ai_center_kind", "single") != "single"
                    or (handoff and handoff["evidence_id"] is not None
                        and st.session_state.get(evidence_key) != handoff["evidence_id"])):
                return {**snapshot, "page": snapshot["origin"]}
        return snapshot
    return None


def render_resume_notice(page):
    if notice := st.session_state.pop(_NOTICE, None):
        st.info(notice)
    if st.session_state.get(_STALE_NOTICE_PAGE) == page:
        st.info(_STALE_NOTICE)
    else:
        st.session_state.pop(_STALE_NOTICE_PAGE, None)


def finish_workspace(slot, page, *, public):
    state = st.session_state
    restored = state.pop(_FORWARD, None)
    snapshot = _capture(page, restored or state.get(_CURRENT))
    if restored:
        state.pop(INPUT, None)
        state.pop(EVENT, None)
        matched = snapshot and all(snapshot.get(key) == restored.get(key) for key in (
            "origin", "demo", "vehicle", "batch", "start", "end", "threshold", "rows", "event"))
        if matched:
            notice = "已恢复此标签页的分析筛选。统计与证据已按当前数据重新计算。"
            state[_CURRENT] = snapshot
            if restored["page"] in (AI, BOGIE, LINKED):
                state[_NOTICE] = notice
                handoff = state[BATCH_HANDOFF]
                if restored.get("event_jump") and handoff.get("event_target"):
                    from rail_vehicle.pages.bogie_link import _open_replay
                    _open_replay("bogie_evidence_target", handoff["event_target"], True, page=restored["page"])
                else:
                    select_workspace_page(restored["page"])
                st.rerun()
            st.caption(notice)
        else:
            state[_STALE_NOTICE_PAGE] = page
            st.info(_STALE_NOTICE)
    state[_CURRENT] = snapshot
    _mount(slot, public=public, snapshot=snapshot)
