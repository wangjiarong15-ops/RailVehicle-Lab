"""Validation of explicitly paired axis readings and calculated projection samples."""

import csv
from decimal import Decimal, DecimalException
from hashlib import sha256
from io import StringIO
import json
from math import fsum, hypot, sqrt

from rail_vehicle.sensor_mapping import number


COLUMNS = ("model_time_s", "reference_axis_m_s2")


def projection_digest(projection):
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


def compare_projection(projection, reference_csv, *, reference_kind, reference_record, alignment_record):
    if (projection.get("schema") != "rail.frame-axis-projection.v1" or
            projection.get("time_basis") != "relative_seconds" or projection.get("data_layer") != "simulation"):
        raise ValueError("需要具有相对时间轴的构架仿真投影 JSON。")
    if reference_kind not in ("synthetic", "field_declared"):
        raise ValueError("请声明参考数据来源。")
    if not all(isinstance(record, str) and 0 < len(record.strip()) <= 200
               for record in (reference_record, alignment_record)):
        raise ValueError("请填写参考记录与时间零点配对依据。")
    rate, values = projection["sample_hz"], projection["axis_accel_m_s2"]
    if type(rate) is not int or not 0 < rate <= 10000 or not 3 <= len(values) <= 10000:
        raise ValueError("投影采样率或长度超出核对范围。")
    if not reference_csv or len(reference_csv) > 512 * 1024:
        raise ValueError("参考 CSV 大小须在 512 KiB 以内。")
    rows = []
    indices = set()
    try:
        reader = csv.reader(StringIO(reference_csv.decode("utf-8-sig"), newline=""), strict=True)
        if tuple(next(reader, [])) != COLUMNS:
            raise ValueError("参考 CSV 表头应为 model_time_s,reference_axis_m_s2。")
        for row in reader:
            if not row:
                continue
            try:
                time_text, reference_text = row
                instant = Decimal(time_text)
                if not instant.is_finite() or instant < 0 or instant >= len(values) or instant.as_tuple().exponent < -9:
                    raise ValueError
                tick = instant * rate
                if tick >= len(values) or tick != tick.to_integral_value():
                    raise ValueError
                index = int(tick)
                reference = number(float(reference_text))
            except (ValueError, DecimalException, OverflowError):
                raise ValueError(f"参考 CSV 第 {reader.line_num} 行需要输出网格上的时刻及有限轴向读值。") from None
            if index in indices:
                raise ValueError("参考 CSV 中有重复配对时刻。")
            indices.add(index)
            calculated = number(values[index])
            rows.append({"reference_csv_line": reader.line_num, "model_sample_index": index,
                         "model_time_s": index / rate, "reference_axis_m_s2": reference,
                         "calculated_axis_m_s2": calculated, "residual_m_s2": number(calculated - reference)})
    except (UnicodeDecodeError, csv.Error):
        raise ValueError("请使用有效的 UTF-8 参考 CSV。") from None
    if len(rows) < 3:
        raise ValueError("至少需要三个唯一配对点。")
    rows.sort(key=lambda row: row["model_sample_index"])
    errors = [row["residual_m_s2"] for row in rows]
    size = len(rows)
    peak = max(map(abs, errors))
    rmse = peak * sqrt(fsum((value / peak)**2 for value in errors) / size) if peak else 0.0
    mae = peak * (fsum(abs(value) / peak for value in errors) / size) if peak else 0.0
    config = projection["configuration"]
    return {
        "schema": "rail.simulation-validation.v1", "independent_measurement": "pending",
        "reference_kind": reference_kind, "reference_record": reference_record.strip(),
        "alignment_record": alignment_record.strip(), "time_basis": "relative_seconds", "source": "simulation",
        "reference_sha256": sha256(reference_csv).hexdigest(),
        "projection_canonical_sha256": projection_digest(projection),
        "source_sha256": projection.get("source_sha256"), "channel": projection["channel"],
        "batch_id": projection["batch_id"], "configuration_version": config["version"],
        "parameters": projection["parameters"], "sample_hz": rate, "solver_version": projection.get("solver_version"),
        "metrics": {"pairs": size, "rmse_m_s2": rmse, "mae_m_s2": mae, "peak_abs_error_m_s2": peak,
                    "pearson_r": _correlation(rows)},
        "pairs": rows,
    }


def _correlation(rows):
    def centered(key):
        values = [row[key] for row in rows]
        scale = max(map(abs, values))
        if not scale:
            return values
        scaled = [value / scale for value in values]
        mean = fsum(scaled) / len(scaled)
        return [value - mean for value in scaled]

    left = centered("reference_axis_m_s2")
    right = centered("calculated_axis_m_s2")
    denominator = hypot(*left) * hypot(*right)
    if not denominator:
        return None
    return max(-1.0, min(1.0, fsum(a*b for a, b in zip(left, right, strict=True)) / denominator))
