"""Describe the observed replay frame for a browser inspection image."""

from rail_vehicle.bogie_replay import GAP, Scene


def inspection_context(*, sample, index: int | None, total: int, threshold: float,
                       scene: Scene, teaching: str, fictional: bool,
                       batch_id: int | None = None) -> dict:
    """Copy the displayed values from a normalized replay sample.

    A long interval retains the preceding row's timestamp and readings. The
    scene label explains that no observation is available at the playhead.
    """
    if sample is None:
        return {"source": "独立教学动画", "scene_label": teaching}

    context = {
        "source": "内置虚构演示（非实际测量）" if fictional else "历史运行批次",
        "scene_label": scene.label,
        "sample_kind": "间隔前最后一次观测" if scene is GAP else "当前观测样本",
        "sample_number": index + 1,
        "sample_count": total,
        "timestamp": sample["timestamp"],
        "speed_kmh": sample["speed_kmh"],
        "lateral_accel": sample["lateral_accel"],
        "vertical_accel": sample["vertical_accel"],
        "threshold": threshold,
    }
    if type(batch_id) is int:
        if fictional and batch_id < 0:
            context["batch_label"] = f"虚构批次 {-batch_id}"
        elif not fictional and batch_id > 0:
            context["batch_label"] = f"历史批次 {batch_id}"
    return context
