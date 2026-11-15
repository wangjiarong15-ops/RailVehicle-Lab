"""Local navigation from an observed threshold event to the bogie replay."""

import streamlit as st

from rail_vehicle.pages.data_workflow import remember_evidence_selection, remember_workbench_selection
from rail_vehicle.ui import BOGIE, navigate


def _open_replay(target_key, target, from_workbench, from_evidence=False):
    if from_workbench:
        remember_workbench_selection()
    if from_evidence:
        remember_evidence_selection(fictional=target["fictional"])
    navigate(BOGIE, **{target_key: target})


def batch_replay_button(container, *, label, key, vehicle_id, batch_id=None, threshold=None,
                        fictional=False, from_workbench=False):
    target = {"vehicle_id": vehicle_id, "batch_id": batch_id}
    if threshold is not None:
        target["threshold"] = threshold
    if fictional:
        target["fictional"] = True
    container.button(label, key=key, width="stretch", on_click=_open_replay,
                     args=("bogie_batch_target", target, from_workbench))


def event_replay_button(*, label, key, vehicle_id, batch_id, threshold, timestamp, axis, fictional=False,
                        row_span=None, from_workbench=False, from_evidence=False):
    target = {"vehicle_id": vehicle_id, "batch_id": batch_id, "threshold": threshold,
              "timestamp": timestamp, "axis": axis, "fictional": fictional}
    if row_span is not None:
        target["row_span"] = row_span
    st.button(label, key=key, on_click=_open_replay,
              args=("bogie_evidence_target", target, from_workbench, from_evidence))
