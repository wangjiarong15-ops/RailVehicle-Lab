"""Inspection exports use displayed observations and their actual provenance."""

import json

from rail_vehicle.bogie_inspection_context import inspection_context
from rail_vehicle.bogie_replay import GAP, LATERAL, gap_cutoff, replay_scene
from rail_vehicle.replay import prepare_replay


def test_teaching_context_uses_scene_name_without_observed_values():
    context = inspection_context(
        sample=None, index=None, total=0, threshold=1.0,
        scene=LATERAL, teaching="横向振动", fictional=False,
    )
    assert context == {"source": "独立教学动画", "scene_label": "横向振动"}


def test_replay_context_preserves_signed_observation_and_labels_fictional_batch():
    sample = {
        "timestamp": "2026-11-13 07:00:01+00:00",
        "speed_kmh": 83.125,
        "lateral_accel": -1.125,
        "vertical_accel": 0.25,
        "file_name": "private.csv",
        "vehicle_code": "private-vehicle",
    }
    context = inspection_context(
        sample=sample, index=1, total=12, threshold=1.0,
        scene=LATERAL, teaching="正常运行", fictional=True, batch_id=-2,
    )
    assert context == {
        "source": "内置虚构演示（非实际测量）",
        "batch_label": "虚构批次 2",
        "scene_label": "横向加速度超阈值",
        "sample_kind": "当前观测样本",
        "sample_number": 2,
        "sample_count": 12,
        "timestamp": "2026-11-13 07:00:01+00:00",
        "speed_kmh": 83.125,
        "lateral_accel": -1.125,
        "vertical_accel": 0.25,
        "threshold": 1.0,
    }
    json.dumps(context, allow_nan=False)


def test_gap_context_keeps_actual_prior_observation_and_does_not_claim_a_current_event():
    track = prepare_replay([
        dict(timestamp=f"2026-11-13T07:00:{second:02d}Z", speed_kmh=83.0,
             lateral_accel=2.0 if second == 2 else 0.0, vertical_accel=0.25)
        for second in (0, 1, 2, 15, 16)
    ])
    position = 6.0
    index = track.index_at(position)
    scene = replay_scene(track, position, index, 1.0, gap_cutoff(track))
    assert scene is GAP
    context = inspection_context(
        sample=track.samples[index], index=index, total=len(track.samples), threshold=1.0,
        scene=scene, teaching="正常运行", fictional=False, batch_id=42,
    )
    assert context["source"] == "历史运行批次"
    assert context["batch_label"] == "历史批次 42"
    assert context["sample_kind"] == "间隔前最后一次观测"
    assert context["scene_label"] == "长采样间隔 · 无当时观测"
    assert context["timestamp"] == "2026-11-13 07:00:02+00:00"
    assert context["sample_number"] == 3
    assert context["lateral_accel"] == 2.0
