"""Whitelisted assembly of AI payloads from already-computed Python results."""

from __future__ import annotations

from typing import Any

from rail_vehicle.ai.schemas import validate_input_payload


def _metric(evidence_id: str, name: str, value: Any, unit: str, method: str) -> dict[str, Any]:
    return {"evidence_id": evidence_id, "name": name, "value": value, "unit": unit, "method": method}


def _threshold_stat(evidence_id: str, name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "name": name,
        "count": result["count"],
        "percentage": result["percentage"],
        "unit": "% of selected samples",
    }


def build_single_batch_payload(
    *,
    analysis: dict[str, Any],
    anomalies: dict[str, Any],
    vehicle: dict[str, Any],
    batch_ref: str,
    selected_start: str,
    selected_end: str,
) -> dict[str, Any]:
    """Select precomputed fields only; never computes statistics or detections."""
    speed = analysis["speed"]
    lateral = analysis["lateral_accel"]
    vertical = analysis["vertical_accel"]
    threshold = analysis["threshold"]
    metrics = [
        _metric("metric.speed.mean", "平均速度", speed["mean"], "km/h", "算术平均"),
        _metric("metric.speed.minimum", "最小速度", speed["minimum"], "km/h", "样本最小值"),
        _metric("metric.speed.maximum", "最大速度", speed["maximum"], "km/h", "样本最大值"),
    ]
    for axis, source, label in (
        ("lateral", lateral, "横向"),
        ("vertical", vertical, "垂向"),
    ):
        prefix = f"metric.accel.{axis}"
        metrics.extend([
            _metric(f"{prefix}.minimum", f"{label}加速度最小值", source["minimum"], "m/s^2", "样本最小值"),
            _metric(f"{prefix}.maximum", f"{label}加速度最大值", source["maximum"], "m/s^2", "样本最大值"),
            _metric(f"{prefix}.mean", f"{label}加速度平均值", source["mean"], "m/s^2", "算术平均"),
            _metric(f"{prefix}.rms", f"{label}加速度 RMS", source["rms"], "m/s^2", "均方根"),
            _metric(f"{prefix}.peak_absolute", f"{label}加速度绝对峰值", source["peak_absolute"], "m/s^2", "绝对值最大样本"),
        ])

    events = []
    counters = {"lateral": 0, "vertical": 0}
    for event in anomalies["events"]:
        axis = "lateral" if event["field"] == "lateral_accel" else "vertical"
        counters[axis] += 1
        events.append({
            "evidence_id": f"event.{axis}.{counters[axis]:03d}",
            "type": axis,
            "start_timestamp": event["start_timestamp"],
            "end_timestamp": event["end_timestamp"],
            "duration_seconds": event["duration_seconds"],
            "sample_count": event["sample_count"],
            "maximum_absolute_value": event["maximum_absolute_value"],
        })
    payload = {
        "schema_version": "railvehicle.ai_input.v1",
        "analysis_type": "single_batch",
        "vehicle": {
            "vehicle_type": vehicle.get("model", vehicle.get("vehicle_type", "")),
            "mass_kg": vehicle.get("mass_kg", vehicle.get("mass")),
            "axle_count": vehicle.get("axle_count"),
            "bogie_count": vehicle.get("bogie_count"),
            "max_speed_kmh": vehicle.get("max_speed_kmh", vehicle.get("max_speed")),
        },
        "selection": {
            "batch_ref": batch_ref,
            "selected_range_utc": {"start": selected_start, "end": selected_end},
        },
        "sample_count": analysis["sample_count"],
        "threshold": {"value": threshold["value"], "unit": "m/s^2", "rule": "abs(value) > threshold"},
        "metrics": metrics,
        "threshold_statistics": [
            _threshold_stat("metric.exceedance.lateral", "横向加速度超阈值", threshold["lateral"]),
            _threshold_stat("metric.exceedance.vertical", "垂向加速度超阈值", threshold["vertical"]),
            _threshold_stat("metric.exceedance.either", "任一加速度超阈值样本", threshold["either"]),
        ],
        "anomaly_events": events,
    }
    return validate_input_payload(payload)


def build_batch_comparison_payload(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact comparison context without IDs, labels, or chart series."""
    batch_fields = (
        ("sample_count", "sample_count", "数据点数量", "点", "样本数量"),
        ("mean_speed_kmh", "speed.mean", "平均速度", "km/h", "算术平均"),
        ("maximum_speed_kmh", "speed.maximum", "最大速度", "km/h", "样本最大值"),
        ("lateral_accel_rms", "accel.lateral.rms", "横向加速度 RMS", "m/s^2", "均方根"),
        ("vertical_accel_rms", "accel.vertical.rms", "垂向加速度 RMS", "m/s^2", "均方根"),
        ("lateral_accel_peak_absolute", "accel.lateral.peak_absolute", "横向加速度绝对峰值", "m/s^2", "绝对值最大样本"),
        ("vertical_accel_peak_absolute", "accel.vertical.peak_absolute", "垂向加速度绝对峰值", "m/s^2", "绝对值最大样本"),
    )
    batches = []
    for index, comparison in enumerate(comparisons, start=1):
        reference = f"B{index}"
        metrics = [
            _metric(f"batch.{reference}.{evidence_suffix}", name, comparison[source], unit, method)
            for source, evidence_suffix, name, unit, method in batch_fields
        ]
        batches.append({"batch_ref": reference, "metrics": metrics})
    return validate_input_payload({
        "schema_version": "railvehicle.ai_input.v1",
        "analysis_type": "batch_comparison",
        "metrics": [],
        "batches": batches,
    })
