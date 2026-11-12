"""Navigation between local vehicle, CSV import and single-batch analysis pages."""

from rail_vehicle.ui import navigate


ANALYSIS_TARGET = "analysis_batch_target"
ANALYSIS_REPLAY_SCOPE = "analysis_replay_scope"
IMPORT_VEHICLE = "import_vehicle_target"


def open_analysis(vehicle_id, batch_id=None, *, threshold=None, scope=None):
    settings = {ANALYSIS_TARGET: {"vehicle_id": vehicle_id, "batch_id": batch_id},
                "dynamics_vehicle_id": vehicle_id}
    if batch_id is not None:
        settings["dynamics_batch_id"] = batch_id
    if scope is not None:
        settings[ANALYSIS_TARGET].update(threshold=threshold, scope=scope)
    navigate("运行分析", **settings)


def analysis_button(container, *, label, key, vehicle_id, batch_id=None, threshold=None, scope=None):
    container.button(label, key=key, width="stretch", on_click=open_analysis, args=(vehicle_id, batch_id),
                     kwargs=dict(threshold=threshold, scope=scope))


def import_button(container, *, label, key, vehicle_id=None):
    container.button(label, key=key, width="stretch", on_click=navigate,
                     args=("CSV 数据导入",), kwargs={IMPORT_VEHICLE: vehicle_id})
