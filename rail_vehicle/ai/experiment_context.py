"""Allowlisted AI contexts for precomputed local experiments, never raw series."""

import json

from rail_vehicle.ai.context import _metric
from rail_vehicle.ai.schemas import AIContractError, validate_input_payload


def _validated(payload):
    validate_input_payload(payload)
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise AIContractError(code="output_contract_invalid") from None
    return payload


def _experiment(method, ranges, limitations):
    return dict(method=method, ranges=ranges, limitations=limitations)


def build_ml_experiment_payload(result):
    """Copy model/rule aggregates; do not fit, score or recalculate percentages."""
    parameters = [
        _metric(f"experiment.ml.{field}", name, result[field], unit, method)
        for field, name, unit, method in (
            ("contamination", "参考离群比例参数", "比例", "参考分数分位阈值参数，不是真实异常率"),
            ("rule_threshold", "双轴加速度规则阈值", "m/s^2", "任一加速度绝对值严格大于阈值"),
            ("score_threshold", "模型分数阈值", "分数", "负的参考模型 offset_"),
            ("tree_count", "树数量", "棵", "IsolationForest 配置"),
            ("seed", "随机种子", "", "可重复实验配置"),
        )
    ]
    target_metrics = [
        _metric(f"batch.B2.ml.{field}", name, result[field], "条", "已计算样本数量")
        for field, name in (("sample_count", "检测样本数"), ("ml_count", "模型标记样本数"),
                             ("rule_count", "规则标记样本数"))
    ]
    target_metrics += [
        _metric(f"batch.B2.ml.count.{key}", label, result["counts"][key], "条", "已计算模型/规则交叉分类数量")
        for key, label in (("both", "模型与规则均标记"), ("ml_only", "仅模型标记"),
                           ("rule_only", "仅规则标记"), ("neither", "两者均未标记"))
    ]
    return _validated(dict(
        schema_version="railvehicle.ai_input.v1", analysis_type="ml_experiment",
        sample_count=result["sample_count"], metrics=parameters,
        batches=[dict(batch_ref="B1", metrics=[_metric("batch.B1.ml.reference_count", "参考样本数", result["reference_count"], "条", "训练样本数量")]),
                 dict(batch_ref="B2", metrics=target_metrics)],
        experiment=_experiment("IsolationForest；仅在 B1 参考批次拟合，B2 仅检测。特征为速度、横向加速度、垂向加速度。",
            [dict(batch_ref="B1", start=result["reference_start"], end=result["reference_end"]),
             dict(batch_ref="B2", start=result["target_start"], end=result["target_end"])],
            ["离群分数不是故障概率，模型标记不是经确认的故障；未标记也不代表安全。",
             "没有真实故障标签，不允许宣称准确率、召回率或真实故障率。参考批次未被认证为正常工况。",
             "模型与规则交叉数量用于观察方法差异，不证明哪种方法更准确。",
             "未提供速度/加速度分布或特征贡献，不能判断模型标记具体由哪个特征引起。",
             "未核实线路、载荷、传感器位置、批次独立性或工况一致性；只能提出待核实假设。"]),
    ))


def build_vibration_experiment_payload(result):
    """Copy available vibration facts; never send waveform/PSD/rolling arrays."""
    metrics = []
    for field, name, unit in (
        ("sample_count", "分析样本数", "条"), ("duration_seconds", "记录跨度", "s"),
        ("sampling_rate_hz", "估算采样率", "Hz"), ("interval_deviation", "采样间隔最大相对偏差", "比例"),
        ("band_low", "频带下限", "Hz"), ("band_high", "频带上限", "Hz"),
        ("window_seconds", "滑动 RMS 窗口", "s"),
    ):
        if result[field] is not None:
            metrics.append(_metric(f"experiment.vibration.{field}", name, result[field], unit, "现有 Python 实验结果/参数"))
    metrics.append(_metric("experiment.vibration.duplicate_rows", "重复 UTC 时间戳", result["sampling"]["duplicate_timestamp_rows"], "条", "已计算额外重复记录数"))
    if result["spectral_available"]:
        for field, name, unit in (("segment_samples", "Welch 分段点数", "点"),
                                   ("segment_count", "Welch 段数", "段"),
                                   ("frequency_resolution_hz", "频率间隔", "Hz")):
            metrics.append(_metric(f"experiment.vibration.{field}", name, result[field], unit, "现有 Python Welch 配置/结果"))
    for field, axis, label in (("lateral_accel", "lateral", "横向"), ("vertical_accel", "vertical", "垂向")):
        channel = result["channels"][field]
        for key, name, unit in (("raw_mean", "原始均值", "m/s^2"), ("raw_rms", "原始 RMS", "m/s^2"),
                                ("dynamic_rms", "去均值 RMS", "m/s^2"), ("dynamic_peak", "去均值绝对峰值", "m/s^2"),
                                ("crest_factor", "峰值因子", "")):
            if channel[key] is not None:
                metrics.append(_metric(f"experiment.vibration.{axis}.{key}", label+name, channel[key], unit, "现有 Python 未加权时域指标"))
        if result["spectral_available"]:
            for key, name, unit in (("dominant_frequency_hz", "频带内 PSD 主峰", "Hz"), ("band_rms", "频带 RMS", "m/s^2")):
                if channel[key] is not None:
                    metrics.append(_metric(f"experiment.vibration.{axis}.{key}", label+name, channel[key], unit, "现有 Python Welch 未加权频域指标"))
    limitations = [
        "只有聚合指标，没有波形、完整频谱或滑动序列；不能推断局部变化时刻、故障频率模式或故障部件。",
        "原始 RMS 与去均值 RMS 口径不同。未实施标准频率加权、Sperling、舒适等级、重力/姿态校正。",
        "未核实传感器安装、轴向、测量链路、抗混叠、线路和载荷。不能推导安全、合规或舒适等级。",
        "未定义或不可计算的指标不提供证据，不能将缺失值当成零。",
        "频带主峰仅为离散频点中 PSD 最大值，受分辨率和谱泄漏影响，不证明共振或故障。",
    ]
    if not result["spectral_available"]:
        limitations += ["频域条件不足，频域分析停用；只能解释已提供的时域和采样指标。"] + list(result["issues"])
    return _validated(dict(
        schema_version="railvehicle.ai_input.v1", analysis_type="vibration_experiment",
        selection=dict(batch_ref="B1", selected_range_utc=dict(start=result["timestamps_utc"][0], end=result["timestamps_utc"][-1])),
        sample_count=result["sample_count"], metrics=metrics,
        experiment=_experiment("未加权时域分析；满足采样条件时采用 Welch 周期 Hann 窗、50% 重叠、每段去均值。",
            [dict(batch_ref="B1", start=result["timestamps_utc"][0], end=result["timestamps_utc"][-1])], limitations),
    ))
