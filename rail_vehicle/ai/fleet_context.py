"""Allowlisted cross-vehicle aggregate context; no raw data or vehicle IDs."""

from rail_vehicle.ai.context import _metric, build_batch_comparison_payload
from rail_vehicle.ai.experiment_context import _validated


def build_fleet_comparison_payload(result):
    """Copy existing fleet statistics; never calculate comparisons or rankings."""
    comparison = result["comparison"]
    payload = build_batch_comparison_payload(comparison["results"])
    payload["analysis_type"] = "fleet_comparison"
    payload["metrics"] = [
        _metric("comparison.speed.minimum", "共同速度筛选下限", result["speed_min"], "km/h", "闭区间筛选参数"),
        _metric("comparison.speed.maximum", "共同速度筛选上限", result["speed_max"], "km/h", "闭区间筛选参数"),
    ]
    for batch, source in zip(payload["batches"], comparison["results"]):
        ref = batch["batch_ref"]
        for field, name, unit in (
            ("source_count", "筛选前样本数", "点"),
            ("retained_fraction", "保留样本比例", "比例"),
            ("duration_seconds", "首末保留样本跨度", "s"),
        ):
            batch["metrics"].append(_metric(f"batch.{ref}.fleet.{field}", name, source[field], unit, "现有 Python 多车辆对比结果"))
        for field, name, unit in (("mass_kg", "登记质量", "kg"), ("axle_count", "轴数", "根"), ("bogie_count", "转向架数量", "个")):
            batch["metrics"].append(_metric(f"batch.{ref}.vehicle.{field}", name, source["vehicle"][field], unit, "登记参数，不代表试验载荷"))
        for field, name in (("interval_median_seconds", "采样间隔中位数"), ("interval_max_seconds", "最大采样间隔"), ("duplicate_timestamp_rows", "重复时间戳记录数")):
            value = source["sampling_summary"][field]
            if value is not None:
                batch["metrics"].append(_metric(f"batch.{ref}.sampling.{field}", name, value, "点" if field == "duplicate_timestamp_rows" else "s", "筛选后现有 Python 采样概览"))
    payload["comparison"] = dict(
        method="各匿名批次对应不同车辆；共同闭区间速度筛选，样本等权统计，不按时间或质量加权。",
        limitations=list(comparison["warnings"]) + [
            "未核实线路、载荷、传感器安装与坐标轴一致性；相同速度区间不代表相同工况。",
            "仅解释已提供的描述性指标，不进行车辆优劣、安全、故障或合规排名。",
            "没有提供成对差值或百分比变化，不得由模型自行计算这些数值。",
            "登记质量不是实测载荷；不能推断质量归一化后的性能差异。",
            "时间跨度包含筛选造成的空档，不是累计运行时长；没有线路位置或同步测量证据。",
        ])
    return _validated(payload)
