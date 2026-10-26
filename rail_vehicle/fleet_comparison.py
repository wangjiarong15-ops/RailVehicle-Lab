"""Descriptive cross-vehicle comparison; never a safety or vehicle ranking."""

import math

from rail_vehicle.batch_comparison import compare_run_batches


def compare_vehicles(entries, speed_min=0.0, speed_max=600.0):
    """Reuse batch statistics on the same closed speed band for each vehicle.

    Gaps left by filtering remain gaps: relative time refers to the first retained
    sample and is never compressed. Empty selections stop the whole comparison.
    """
    if not (math.isfinite(speed_min) and math.isfinite(speed_max)
            and 0 <= speed_min <= speed_max <= 600):
        raise ValueError("速度区间必须满足 0 ≤ 下限 ≤ 上限 ≤ 600 km/h。")
    if not 2 <= len(entries) <= 6:
        raise ValueError("请选择 2–6 辆不同车辆。")
    ids = [entry["vehicle"]["id"] for entry in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("同一车辆不能重复参与对比。")
    batches = []
    for entry in entries:
        selected = []
        for row in entry["samples"]:
            speed = float(row["speed_kmh"])
            if not math.isfinite(speed):
                raise ValueError("运行速度包含无效数值。")
            if speed_min <= speed <= speed_max:
                selected.append(row)
        if not selected:
            raise ValueError("至少一辆车在当前速度区间内没有数据，请调整区间或批次。")
        batches.append(dict(batch_id=entry["batch_id"],
                            label=entry["vehicle"]["vehicle_code"], samples=selected))
    results = compare_run_batches(batches)
    warnings = ["相同速度区间不代表相同线路、载荷、传感器位置或运行工况；本页不进行车辆优劣或安全排名。"]
    for entry, result in zip(entries, results):
        result["vehicle"] = {field: entry["vehicle"][field] for field in
                             ("id", "vehicle_code", "vehicle_type", "mass_kg", "axle_count", "bogie_count")}
        result["source_count"] = len(entry["samples"])
        result["retained_fraction"] = result["sample_count"] / result["source_count"]
        result["duration_seconds"] = result["series"][-1]["relative_time_seconds"]
    for field, label in (("vehicle_type", "车型"), ("mass_kg", "登记质量"),
                         ("axle_count", "轴数"), ("bogie_count", "转向架数量")):
        if len({entry["vehicle"][field] for entry in entries}) > 1:
            warnings.append(f"参与车辆的{label}不同，不能直接归因于车辆性能。")
    sampling = [result["sampling_summary"] for result in results]
    if len({s["interval_median_seconds"] for s in sampling}) > 1:
        warnings.append("采样间隔中位数不同，样本加权统计的可比性受限。")
    if any(s["duplicate_timestamp_rows"] for s in sampling):
        warnings.append("存在重复时间戳；重复样本仍按独立记录计入统计。")
    return dict(results=results, warnings=warnings)
