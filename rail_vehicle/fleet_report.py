"""Cross-vehicle PDF assembly from precomputed results, never AI or statistics."""

from rail_vehicle.experiment_report import _base, _chart, _fact, _finish, _number


def _display(value):
    if value is None:
        return "不适用/未定义"
    return format(_number(value), ".8g")


def build_fleet_report(result, fingerprint, selection, *, generated_at=None, ai_context=None, ai_result=None):
    report = _base("fleet", fingerprint, selection, generated_at)
    report["title"] = "轨道车辆多车辆运行表现对比报告"
    comparison = result["comparison"]
    vehicles = comparison["results"]
    if not 2 <= len(vehicles) <= 6:
        raise ValueError("多车辆报告需要 2–6 辆车。")
    report["ranges"] = dict(对齐口径="每辆车首个保留样本为 0 秒，保留真实时间间隔")
    _fact(report, "report.fleet.speed.minimum", "共同速度筛选下限（含边界）", result["speed_min"], "km/h")
    _fact(report, "report.fleet.speed.maximum", "共同速度筛选上限（含边界）", result["speed_max"], "km/h")
    report["tables"] = []
    for index, row in enumerate(vehicles, 1):
        ref = f"B{index}"
        # Only these local identification fields are written to PDF; never arbitrary notes or filenames.
        report["tables"].append(dict(title=f"{ref} · 车辆与批次映射（本地报告）", widths=[.32, .68], rows=[
            ["字段", "当前选择"], ["车辆编号", str(row["label"])], ["运行批次", str(row["batch_id"])],
            ["车型", str(row["vehicle"]["vehicle_type"])],
            ["保留样本开始（UTC）", row["selected_start_utc"]],
            ["保留样本结束（UTC）", row["selected_end_utc"]],
        ]))
    fields = [
        ("sample_count", "保留样本数（点）"), ("source_count", "筛选前样本数（点）"),
        ("retained_fraction", "保留比例（0–1）"), ("duration_seconds", "首末跨度（s）"),
        ("mean_speed_kmh", "平均速度（km/h）"), ("maximum_speed_kmh", "最大速度（km/h）"),
        ("lateral_accel_rms", "横向 RMS（m/s^2）"), ("vertical_accel_rms", "垂向 RMS（m/s^2）"),
        ("lateral_accel_peak_absolute", "横向绝对峰值（m/s^2）"), ("vertical_accel_peak_absolute", "垂向绝对峰值（m/s^2）"),
    ]
    # Groups of at most three vehicles keep the comparison readable on A4.
    for start in range(0, len(vehicles), 3):
        group = vehicles[start:start+3]
        columns = [f"B{start+i+1}" for i in range(len(group))]
        rows = [["指标 / 口径", *columns]]
        rows.extend([[label, *[_display(row[field]) for row in group]] for field, label in fields])
        rows.extend([[label, *[_display(row["vehicle"][field]) for row in group]] for field, label in
                     (("mass_kg", "登记质量（kg）"), ("axle_count", "轴数"), ("bogie_count", "转向架数量"))])
        rows.extend([[label, *[_display(row["sampling_summary"][field]) for row in group]] for field, label in
                     (("interval_median_seconds", "间隔中位数（s）"), ("interval_max_seconds", "最大间隔（s）"),
                      ("duplicate_timestamp_rows", "重复时间戳记录数（点）"))])
        report["tables"].append(dict(title="统计与采样对比 · " + " / ".join(columns), rows=rows,
                                     widths=[.4]+[.6/len(group)]*len(group)))
    for field, title, unit in (("speed_kmh", "速度对比", "速度（km/h）"),
                               ("lateral_accel", "横向加速度对比", "横向加速度（m/s^2）"),
                               ("vertical_accel", "垂向加速度对比", "垂向加速度（m/s^2）")):
        series = []
        source_points = 0
        for index, row in enumerate(vehicles, 1):
            chart = _chart(title, [p["relative_time_seconds"] for p in row["series"]],
                           [p[field] for p in row["series"]], "相对首个保留样本（秒）", unit)
            series.append(dict(label=f"B{index}", points=chart["points"]))
            source_points += chart["source_points"]
        report["charts"].append(dict(title=title, x_label="相对首个保留样本（秒）", y_label=unit,
                                     series=series, points=[p for s in series for p in s["points"]], source_points=source_points))
    report["notes"] = list(comparison["warnings"]) + [
        "均值、RMS 和绝对峰值使用筛选后完整样本的现有 Python 结果；按样本等权，不按时间或质量归一化。",
        "登记质量不是实测载荷。采样间隔描述唯一 UTC 时刻之间的间隔，重复记录仍按样本计入统计。",
        "首末跨度包含速度筛选留下的空档，不是累计运行时长。散点不代表同位置、同步测量或同工况。",
        "每辆车选一个批次，不输出车辆优劣、故障、安全、合规或标准舒适性排名。",
    ]
    return _finish(report, ai_context, ai_result, selection)
