"""Local navigation from an observed threshold event to the bogie replay."""

import streamlit as st

from rail_vehicle.ui import navigate


def batch_replay_button(container, *, label, key, vehicle_id, batch_id=None):
    container.button(label, key=key, width="stretch", on_click=navigate,
                     args=("三维转向架仿真",),
                     kwargs={"bogie_batch_target": {"vehicle_id": vehicle_id, "batch_id": batch_id}})


def event_replay_button(*, label, key, vehicle_id, batch_id, threshold, timestamp, axis):
    st.button(label, key=key, on_click=navigate, args=("三维转向架仿真",),
              kwargs={"bogie_evidence_target": {
                  "vehicle_id": vehicle_id, "batch_id": batch_id, "threshold": threshold,
                  "timestamp": timestamp, "axis": axis,
              }})
