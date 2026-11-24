"""Versioned declarations and checks for engineering acquisition files."""

import json
import re

from rail_vehicle.data_quality import review_sampling_timestamps
from rail_vehicle.sensor_mapping import AXES, CHANNELS, PARTS, number, vector
from rail_vehicle.timestamp_utils import parse_iso_timestamp


SCHEMA = "rail.engineering-intake.v1"
SOURCE_LABELS = {"synthetic": "虚构工程演练", "field_declared": "用户声明现场采集"}
CONFIG_LABELS = {"missing": "缺少当前配置", "mismatch": "登记配置与接入声明不一致", "matches": "登记配置与接入声明一致"}
CSV_UNITS = {"speed_kmh": "km/h", **dict.fromkeys(CHANNELS, "m/s²")}
MAX_MANIFEST_BYTES = 64 * 1024


def validate_manifest(manifest):
    fields = {"schema", "source_kind", "source_record", "source_sha256", "vehicle_code",
              "sample_rate_hz", "time_basis", "units", "sensors"}
    if not isinstance(manifest, dict) or not fields <= manifest.keys() or manifest.keys() - fields - {"clock_offset_s"}:
        raise ValueError("工程元数据字段不完整或包含未知字段。请使用 V0.5 模板。")
    if manifest["schema"] != SCHEMA or manifest["source_kind"] not in tuple(SOURCE_LABELS):
        raise ValueError("工程元数据版本或数据来源类型不受支持。")
    for field in ("source_record", "vehicle_code"):
        value = manifest[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise ValueError(f"{field} 需要 1 至 120 个字符的记录。")
    digest = manifest["source_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("工程元数据缺少有效的源 CSV SHA-256。")
    rate = number(manifest["sample_rate_hz"])
    if not 0.000001 <= rate <= 1_000_000:
        raise ValueError("声明采样频率须在 0.000001 至 1,000,000 Hz，符合当前 CSV 时间分辨率和检查范围。")
    basis = manifest["time_basis"]
    if basis not in ("utc", "device_offset") or ("clock_offset_s" in manifest) != (basis == "device_offset"):
        raise ValueError("时间基准须声明为 utc 或 device_offset。仅 device_offset 必须填写固定时差补偿。")
    if manifest["units"] != CSV_UNITS:
        raise ValueError("CSV 单位须为速度 km/h、两路加速度 m/s²。请先换算并记录转换来源。")
    sensors = manifest["sensors"]
    if not isinstance(sensors, dict) or sensors.keys() != CHANNELS.keys():
        raise ValueError("工程元数据须分别声明横向和垂向加速度测点。")
    normalized = {}
    for channel, sensor in sensors.items():
        required = {"sensor_id", "part_id", "position_frame", "position_m", "axis"}
        if not isinstance(sensor, dict) or not required <= sensor.keys() or sensor.keys() - required - {"full_scale_m_s2"}:
            raise ValueError(f"{channel} 缺少传感器编号、部件、坐标系、位置或局部正方向。")
        if (not isinstance(sensor["sensor_id"], str) or not re.fullmatch(r"[\w.-]{1,64}", sensor["sensor_id"])
                or sensor["part_id"] not in tuple(PARTS) or sensor["position_frame"] not in ("vehicle", "model")
                or sensor["axis"] not in AXES):
            raise ValueError(f"{channel} 的传感器编号、部件、坐标系或局部正方向无效。")
        normalized[channel] = {**sensor, "position_m": vector(sensor["position_m"])}
        if "full_scale_m_s2" in sensor:
            full_scale = number(sensor["full_scale_m_s2"])
            if full_scale <= 0:
                raise ValueError(f"{channel} 的声明满量程须为正数。")
            normalized[channel]["full_scale_m_s2"] = full_scale
    first, second = normalized.values()
    if first["sensor_id"] == second["sensor_id"] and first["axis"][-1] == second["axis"][-1]:
        raise ValueError("同一传感器的两个通道须使用不同的局部轴。")
    clean = {**manifest, "source_sha256": digest.lower(), "sample_rate_hz": rate, "sensors": normalized}
    if basis == "device_offset":
        clean["clock_offset_s"] = number(manifest["clock_offset_s"])
    return clean


def load_manifest(content):
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("工程元数据 JSON 大小上限为 64 KiB。")
    try:
        def unique_fields(pairs):
            fields = dict(pairs)
            if len(fields) != len(pairs):
                raise ValueError("duplicate metadata field")
            return fields

        manifest = json.loads(content.decode("utf-8-sig"), object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        raise ValueError("请上传有效的 UTF-8 工程元数据 JSON。") from None
    return validate_manifest(manifest)


def assess_intake(validation, manifest):
    """Use the same validated CSV bytes that will be written in the import transaction."""
    manifest = validate_manifest(manifest)
    if not validation.is_valid:
        raise ValueError("请先修复 CSV 的缺值或格式错误。")
    if manifest["source_sha256"] != validation.source_sha256:
        raise ValueError("工程元数据的 SHA-256 与当前运行 CSV 不匹配。")
    if set(validation.sampling_by_vehicle) != {manifest["vehicle_code"]}:
        raise ValueError("工程元数据只适用于声明的单车文件。多车原始文件可按普通 CSV 导入。")
    if validation.naive_timestamp_rows:
        raise ValueError(f"工程 CSV 中有 {validation.naive_timestamp_rows} 行时间戳缺少显式时区。")
    period = 1 / manifest["sample_rate_hz"]
    full_scales = {channel: spec.get("full_scale_m_s2") for channel, spec in manifest["sensors"].items()}
    counts = {channel: 0 if scale is not None else None for channel, scale in full_scales.items()}

    def timestamps():
        for row in validation.iter_rows():
            for channel, scale in full_scales.items():
                if scale is not None and abs(row[channel]) >= scale:
                    counts[channel] += 1
            yield row["timestamp"]

    quality = review_sampling_timestamps(timestamps(),
                                         expected_interval_seconds=period, detail_limit=20,
                                         flag_short_intervals=True)
    quality["duplicate_row_fraction"] = quality["duplicate_timestamp_rows"] / quality["sample_count"]
    median = quality["interval_median_seconds"]
    quality["median_implied_rate_hz"] = 1 / median if median else None
    quality["full_scale_candidate_counts"] = counts
    return {"manifest": manifest, "quality": quality, "missing_by_field": dict(validation.missing_by_field),
            "clock_drift_evidence": "insufficient", "independent_measurement": "pending"}


def configuration_review(intake, channels):
    """Compare declared source metadata with current versioned registrations."""
    manifest = intake["manifest"]
    review = []
    for channel, declared in manifest["sensors"].items():
        binding = channels.get(channel)
        metadata = (binding or {}).get("metadata")
        if metadata is None:
            differences = []
            status = "missing"
        else:
            expected = {k: declared[k] for k in ("part_id", "position_frame", "position_m", "axis")}
            expected.update(unit=manifest["units"][channel], rate_hz=manifest["sample_rate_hz"],
                            time_basis=manifest["time_basis"], clock_offset_s=manifest.get("clock_offset_s"))
            differences = [key for key, value in expected.items() if metadata.get(key) != value]
            if binding["serial"] != declared["sensor_id"]:
                differences.append("sensor_id")
            origin = "illustration" if manifest["source_kind"] == "synthetic" else "recorded"
            if metadata.get("origin") != origin:
                differences.append("origin")
            differences.extend(key for key in ("mount_status", "calibration", "clock_status")
                               if metadata.get(key) != "verified")
            status = "mismatch" if differences else "matches"
        review.append({"channel": channel, "status": status, "differences": differences,
                       "version": binding["version"] if metadata is not None else None})
    return review


def source_label(snapshot, fallback="历史导入 · 数据来源未声明"):
    intake = snapshot.get("engineering")
    return SOURCE_LABELS[intake["manifest"]["source_kind"]] if intake else fallback


def report_provenance(snapshot, *, fallback="历史导入 · 数据来源未声明", source_sha256=None):
    intake = snapshot.get("engineering")
    channels = snapshot.get("channels", {})
    configurations = []
    if intake:
        configurations = configuration_review(intake, channels)
    else:
        for channel in CHANNELS:
            binding = channels.get(channel)
            metadata = (binding or {}).get("metadata")
            configurations.append({"channel": channel, "version": binding["version"] if metadata is not None else None,
                                   "status": metadata.get("calibration", "unknown") if metadata is not None else "missing",
                                   "differences": []})
    return {"source": source_label(snapshot, fallback), "source_sha256": snapshot.get("source_sha256") or source_sha256,
            "source_record": intake["manifest"]["source_record"] if intake else None,
            "intake": intake, "configurations": configurations, "independent_measurement": "pending"}
