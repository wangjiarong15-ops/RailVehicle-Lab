"""Compare a registered accelerometer channel against independently paired reference points."""

import csv
from datetime import timezone
from hashlib import sha256
from io import StringIO
from math import hypot, sqrt

from rail_vehicle.sensor_mapping import acceleration_si, aligned_time, dot, number, registration
from rail_vehicle.sensor_store import active_channels
from rail_vehicle.timestamp_utils import parse_iso_timestamp


REFERENCE_COLUMNS = (
    "source_sha256", "csv_line", "reference_utc",
    "model_ax_m_s2", "model_ay_m_s2", "model_az_m_s2",
)
MAX_REFERENCE_BYTES = 512 * 1024
MAX_REFERENCE_POINTS = 2000
REFERENCE_STATUS = {
    "within_limits": "所选参考点在设定误差内",
    "outside_limits": "至少一个配对点超出设定误差",
    "insufficient_excitation": "参考数据的双向激励不足",
}


def reference_scope(snapshot, channel, binding, samples):
    """Identity for the immutable, chronologically ordered batch analysis window."""
    return (snapshot["vehicle_id"], snapshot["batch_id"], channel, binding["version"],
            snapshot.get("source_sha256"), samples[0].get("sample_id"), samples[-1].get("sample_id"), len(samples))


def matching_reference(saved, snapshot, samples):
    """Use only a session result for the current source, window and active configuration."""
    if not saved or not samples:
        return None
    scope, evidence = saved
    channel = evidence["channel"]
    binding = active_channels(snapshot).get(channel)
    if binding and scope == reference_scope(snapshot, channel, binding, samples) and binding == evidence["configuration"]:
        return evidence
    return None


def _reference_points(content, source_digest):
    if not content or len(content) > MAX_REFERENCE_BYTES:
        raise ValueError("参考 CSV 不能为空，大小上限为 512 KiB。")
    points = {}
    seen_times = set()
    try:
        reader = csv.reader(StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        if tuple(next(reader, [])) != REFERENCE_COLUMNS:
            raise ValueError("参考 CSV 表头与模板不一致。")
        for row in reader:
            if not row:
                continue
            if len(row) != len(REFERENCE_COLUMNS):
                raise ValueError(f"参考 CSV 第 {reader.line_num} 行列数错误。")
            digest, raw_line, stamp, *components = row
            if digest.strip().lower() != source_digest:
                raise ValueError(f"参考 CSV 第 {reader.line_num} 行的源文件 SHA-256 不匹配。")
            try:
                source_line = int(raw_line)
                if source_line < 2 or ("T" not in stamp and " " not in stamp):
                    raise ValueError
                instant = parse_iso_timestamp(stamp)
                if instant.tzinfo is None:
                    raise ValueError
                instant = instant.astimezone(timezone.utc)
                acceleration = [number(float(v)) for v in components]
            except (ValueError, OverflowError):
                raise ValueError(f"参考 CSV 第 {reader.line_num} 行需要有效的原始行号、带时区时间和有限加速度。") from None
            if source_line in points or instant in seen_times:
                raise ValueError("参考 CSV 含重复的原始行号或参考时间，无法建立一对一配对。")
            if len(points) == MAX_REFERENCE_POINTS:
                raise ValueError("参考 CSV 最多可包含 2,000 个配对点。")
            points[source_line] = (instant, acceleration)
            seen_times.add(instant)
    except (UnicodeDecodeError, csv.Error):
        raise ValueError("参考文件需要使用 UTF-8 和有效的 CSV 格式。") from None
    if len(points) < 3:
        raise ValueError("至少需要 3 个独立参考点。")
    return points


def _compare_points(samples, points, channel, metadata, direction):
    rows = []
    matched_lines = set()
    observed_times = set()
    for sample in samples:
        line = sample.get("csv_line")
        if line not in points:
            continue
        if line in matched_lines:
            raise ValueError("当前车辆样本中有重复的原始 CSV 行，无法建立一对一配对。")
        matched_lines.add(line)
        reference_time, reference_model = points[line]
        stamp = sample.get("source_time", sample["timestamp"])
        aligned = aligned_time(stamp, metadata)
        if aligned is None:
            raise ValueError(f"原始 CSV 第 {line} 行无法换算为 UTC。")
        if aligned in observed_times:
            raise ValueError("配对样本包含重复的对齐时刻，无法核查共同时间基准。")
        observed_times.add(aligned)
        try:
            expected = number(dot(reference_model, direction))
            measured = acceleration_si(sample[channel], metadata["unit"], metadata["gain"], metadata["bias"])
            error = number(measured - expected)
        except (ValueError, OverflowError):
            raise ValueError(f"原始 CSV 第 {line} 行换算或比对超出数值范围。") from None
        delta_ms = (parse_iso_timestamp(aligned) - reference_time).total_seconds() * 1000
        rows.append(dict(csv_line=line, sample_id=sample.get("sample_id"), sensor_time=stamp,
                         aligned_utc=aligned, reference_utc=reference_time.isoformat(),
                         reference_model_m_s2=reference_model, raw_m_s2=sample[channel],
                         expected_m_s2=expected, measured_m_s2=measured, error_m_s2=error, delta_ms=delta_ms))
    if len(rows) != len(points):
        raise ValueError("参考文件中有原始行不属于当前车辆及分析窗口，请核对批次或调整时间筛选。")
    return rows


def check_reference(snapshot, channel, samples, content, *, reference_record, acceleration_limit,
                    time_limit_ms, min_excitation):
    """Use declared pairings and tolerances without adjusting the saved configuration."""
    acceleration_limit, time_limit_ms, min_excitation = map(number, (acceleration_limit, time_limit_ms, min_excitation))
    if acceleration_limit < 0 or time_limit_ms < 0 or min_excitation <= acceleration_limit:
        raise ValueError("允许误差须为非负，最小双向激励须大于允许加速度误差。")
    reference_record = reference_record.strip()
    if not reference_record or len(reference_record) > 200:
        raise ValueError("请填写独立参考仪器或试验记录编号。")

    binding = active_channels(snapshot).get(channel)
    if not binding or not snapshot.get("source_sha256"):
        raise ValueError("需要已有通道配置和保存了原始文件摘要的导入批次。")
    metadata = binding["metadata"]
    spatial = registration(metadata)
    direction = spatial["direction"]
    if direction is None or spatial["position"] is None or metadata.get("clock_status") != "verified":
        raise ValueError("请先登记并核验安装点、模型系测量轴及时间基准。")

    points = _reference_points(content, snapshot["source_sha256"])
    rows = _compare_points(samples, points, channel, metadata, direction)
    expected = [row["expected_m_s2"] for row in rows]
    min_expected, max_expected = min(expected), max(expected)
    has_excitation = min_expected <= -min_excitation and max_expected >= min_excitation
    max_acceleration_error = max(abs(row["error_m_s2"]) for row in rows)
    max_time_error = max(abs(row["delta_ms"]) for row in rows)
    scale = sqrt(len(rows))
    rms_error = hypot(*(row["error_m_s2"] / scale for row in rows))
    if not has_excitation:
        status = "insufficient_excitation"
    elif max_acceleration_error > acceleration_limit or max_time_error > time_limit_ms:
        status = "outside_limits"
    else:
        status = "within_limits"
    return dict(
        schema="rail.calibration-check.v1", status=status, batch_id=snapshot["batch_id"],
        vehicle_id=snapshot["vehicle_id"], vehicle_code=snapshot["vehicle_code"],
        source_file=snapshot["file_name"], source_sha256=snapshot["source_sha256"], channel=channel,
        configuration=binding, registration=spatial, reference_record=reference_record,
        reference_sha256=sha256(content).hexdigest(),
        method="projection of reference model acceleration onto registered unit axis; paired by original CSV line",
        limits=dict(max_acceleration_error_m_s2=acceleration_limit, max_time_error_ms=time_limit_ms,
                    min_excitation_each_sign_m_s2=min_excitation),
        metrics=dict(pair_count=len(rows), min_reference_m_s2=min_expected, max_reference_m_s2=max_expected,
                     max_acceleration_error_m_s2=max_acceleration_error, rms_error_m_s2=rms_error,
                     max_time_error_ms=max_time_error),
        pairs=rows,
    )
