"""Local-only provenance. Native batch statistics continue using the imported SI channel."""

from rail_vehicle.sensor_mapping import CHANNELS, PARTS, acceleration_si, aligned_time, registration
from rail_vehicle.sensor_store import active_channels


def observation(snapshot, channel, sample, threshold, *, gap=False):
    record = active_channels(snapshot).get(channel)
    m = record["metadata"] if record else {}
    raw = sample[channel]
    aligned = aligned_time(sample.get("source_time", sample["timestamp"]), m)
    space = registration(m)
    calibrated = acceleration_si(raw, m["unit"], m["gain"], m["bias"]) if space["vehicle_direction"] else None
    return {
        "batch_id": snapshot.get("batch_id"), "vehicle_code": snapshot.get("vehicle_code"),
        "file_name": snapshot.get("file_name"), "sha256": snapshot.get("source_sha256"),
        "csv_line": sample.get("csv_line"), "database_sample_id": sample.get("sample_id"),
        "channel": channel, "serial": record and record["serial"],
        "version": record and record["version"], "part_id": m.get("part_id"), "origin": m.get("origin"),
        "source_reference": m.get("source"), "time_basis": m.get("time_basis"), "sample_rate_hz": m.get("rate_hz"),
        "clock_status": m.get("clock_status", "unknown"),
        "configuration_status": m.get("calibration", "unknown") if record else "unconfigured",
        "coordinate_status": "model_axis_registered" if space["direction"] else
                             "vehicle_axis_only" if space["vehicle_direction"] else "unknown",
        "mount_verified": space["position"] is not None, "model_direction": space["direction"],
        "vehicle_direction": space["vehicle_direction"], "csv_time": sample.get("source_time", sample["timestamp"]),
        "synchronized_utc": aligned, "raw_si": raw, "calibrated_axis_si": calibrated,
        "threshold_si": threshold, "method": "abs(CSV m/s²) > 阈值",
        "exceeded": None if gap else abs(raw) > threshold, "gap": gap,
    }


def viewer_sensors(snapshot, selected):
    sensors = []
    for channel, r in active_channels(snapshot).items():
        m = r["metadata"]
        sensors.append(dict(channel=channel, label=CHANNELS[channel], serial=r["serial"], version=r["version"],
                            part=m.get("part_id"), illustration=m.get("origin") == "illustration",
                            **registration(m)))
    return {"selected": selected, "channels": sensors}


def supports_frame_projection(binding):
    m = (binding or {}).get("metadata") or {}
    mapped = registration(m)
    return (PARTS.get(m.get("part_id"), {}).get("group") == "构架" and
            mapped["position"] is not None and mapped["direction"] is not None)


def project_frame_response(snapshot, channel, version, simulation):
    """Observe rigid-frame vertical translation along a registered sensor axis."""
    binding = active_channels(snapshot).get(channel)
    if not binding or binding["version"] != version or not supports_frame_projection(binding):
        raise ValueError("需要当前版本已登记并标定的构架刚体测点。")
    if simulation["source"] != "calculated" or simulation["scenario"] not in ("level", "rough", "flat", "soft"):
        raise ValueError("轴向投影仅支持单自由度垂向工况。")
    direction = registration(binding["metadata"])["direction"]
    return dict(schema="rail.frame-axis-projection.v1", source="简化动力学计算", vehicle_code=snapshot["vehicle_code"],
                batch_id=snapshot["batch_id"], channel=channel, configuration=binding, model_direction=direction,
                solver_version=simulation["version"], parameters=simulation["parameters"], time_basis="relative_seconds",
                sample_hz=simulation["sample_hz"], method="a_axis = a_frame_z * direction_model_z",
                axis_accel_m_s2=[az*direction[2] for az in simulation["channels"]["accel_m_s2"]])


def scope_evidence(snapshot, channel, ordered, metrics, threshold, source):
    """Snapshot the actual input rows and binding used to explain an existing calculation."""
    binding = active_channels(snapshot).get(channel)
    lines = [s.get("csv_line") for s in ordered]
    return dict(schema="rail.sensor-evidence.v1", source=source, batch_id=snapshot.get("batch_id"),
                vehicle_code=snapshot.get("vehicle_code"), file_name=snapshot.get("file_name"),
                sha256=snapshot.get("source_sha256"), channel=channel, configuration=binding,
                registration=registration(binding["metadata"] if binding else {}),
                database_sample_ids=[s.get("sample_id") for s in ordered],
                csv_lines_in_analysis_order=lines, source_rows_known=all(n is not None for n in lines),
                samples=len(ordered), time_first=ordered[0]["timestamp"], time_last=ordered[-1]["timestamp"],
                time_basis=(binding or {}).get("metadata", {}).get("time_basis"),
                clock_status=(binding or {}).get("metadata", {}).get("clock_status", "unknown"),
                rms_si=metrics["rms"], method="sqrt(mean(CSV m/s² squared))",
                threshold_si=threshold, threshold_method="abs(CSV m/s²) > threshold",
                exceeded_csv_lines=[n for n, s in zip(lines, ordered, strict=True) if abs(s[channel]) > threshold])
