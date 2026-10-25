"""Unweighted vibration exploration with explicit sampling eligibility checks.

This module does not implement a comfort standard, frequency weighting,
Sperling index, or operational safety classification.
"""

from datetime import timezone
import hashlib
import json
import math

from rail_vehicle.data_quality import analyze_sampling_times
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.timestamp_utils import parse_iso_timestamp


STATE_KEY = "vibration_analysis_result"
MAX_SAMPLES = 200_000
MIN_SPECTRAL_SAMPLES = 64
MAX_INTERVAL_DEVIATION = 0.01
CHANNELS = ("lateral_accel", "vertical_accel")


class VibrationUnavailableError(RuntimeError):
    """Optional scientific dependencies are unavailable."""


class VibrationInputError(ValueError):
    """Fixed, safe input validation feedback."""


def signal_libraries():
    try:
        import numpy as np
        from scipy.signal import welch
    except ImportError:
        raise VibrationUnavailableError("振动分析依赖未安装，请安装 signal 可选依赖。") from None
    return np, welch


def vibration_fingerprint(samples, selection, *, band_low, band_high, window_seconds):
    encoded = json.dumps([samples, selection, band_low, band_high, window_seconds],
                         sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analyze_vibration(samples, *, band_low=0.5, band_high=20.0, window_seconds=1.0):
    """Compute raw and demeaned statistics; gate Welch spectra on time quality.

    Sampling rate = inverse median UTC interval. Spectral eligibility requires
    distinct timestamps, <=1% maximum interval deviation, >=64 rows, a band
    strictly below Nyquist, and >=5 cycles at the requested lowest frequency.
    These are conservative product gates, not requirements of a rail standard.
    """
    for value in (band_low, band_high, window_seconds):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise VibrationInputError("频带与滑动窗口必须为有限数字。")
    if not 0 < band_low < band_high or window_seconds <= 0:
        raise VibrationInputError("频带须满足 0 < 下限 < 上限，滑动窗口须大于 0 秒。")
    if not samples or len(samples) > MAX_SAMPLES:
        raise VibrationInputError(f"请选择 1 至 {MAX_SAMPLES:,} 条运行样本。")
    try:
        analysis = analyze_run_data(samples)
        ordered = analysis["samples"]
        sampling = analyze_sampling_times(ordered)
        timestamps = []
        for row in ordered:
            instant = parse_iso_timestamp(row["timestamp"])
            timestamps.append(instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None
                              else instant.astimezone(timezone.utc))
    except (ValueError, TypeError, KeyError, OverflowError):
        raise VibrationInputError("运行数据包含无效时间戳或数值，无法分析。") from None
    if any(not 0 <= float(row["speed_kmh"]) <= 600 or
           any(abs(float(row[field])) > 100 for field in CHANNELS) for row in ordered):
        raise VibrationInputError("运行数据数值超出 CSV 导入允许范围。")
    np, welch = signal_libraries()
    offsets = [(instant - timestamps[0]).total_seconds() for instant in timestamps]
    interval = sampling["interval_median_seconds"]
    rate = 1 / interval if interval else None
    deviation = (max(abs(sampling["interval_min_seconds"] - interval),
                     abs(sampling["interval_max_seconds"] - interval)) / interval) if interval else None
    issues = []
    if sampling["duplicate_timestamp_rows"]:
        issues.append("存在重复 UTC 时间戳，未去重或补造样本；频域分析已停用。")
    if interval is None:
        issues.append("没有有效采样间隔，无法估算采样率。")
    if deviation is not None and deviation > MAX_INTERVAL_DEVIATION + 1e-9:
        issues.append("采样间隔相对中位数的最大偏差超过 1%，未重采样；频域分析已停用。")
    if len(ordered) < MIN_SPECTRAL_SAMPLES:
        issues.append("频域分析至少需要 64 条样本。")
    if rate and band_high >= rate / 2:
        issues.append("所选频带上限须严格小于采样率的一半（奈奎斯特频率），请提高原始采样率或缩小频带。")
    if offsets[-1] < 5 / band_low:
        issues.append("记录时长不足所选最低频率的 5 个周期，请扩大时间范围或调整频带。")
    # A fixed short segment at high sampling rates cannot resolve the requested
    # low frequencies. Use at least five cycles per segment, without padding.
    nperseg = (min(len(ordered), max(256, math.ceil(5 * rate / band_low)))
               if not issues else min(1024, len(ordered)))
    resolution = rate / nperseg if rate else None
    output = dict(
        sample_count=len(ordered), duration_seconds=offsets[-1], sampling=sampling,
        sampling_rate_hz=rate, interval_deviation=deviation, issues=issues,
        spectral_available=not issues, band_low=band_low, band_high=band_high,
        frequency_resolution_hz=resolution, segment_samples=nperseg,
        segment_count=1 + (len(ordered) - nperseg) // (nperseg - nperseg // 2),
        window_seconds=window_seconds, channels={},
        timestamps_utc=[instant.isoformat(sep=" ") for instant in timestamps], offsets=offsets,
    )
    for field in CHANNELS:
        values = np.asarray([float(row[field]) for row in ordered], dtype=float)
        raw = analysis[field]
        centered = values - raw["mean"] if np.ptp(values) else np.zeros_like(values)
        dynamic_rms = float(np.sqrt(np.mean(centered * centered)))
        dynamic_peak = float(np.max(np.abs(centered)))
        channel = dict(raw_mean=raw["mean"], raw_rms=raw["rms"], raw_peak=raw["peak_absolute"],
                       dynamic_rms=dynamic_rms, dynamic_peak=dynamic_peak,
                       crest_factor=dynamic_peak / dynamic_rms if dynamic_rms else None,
                       values=values.tolist(), dominant_frequency_hz=None, band_rms=None,
                       frequencies=[], psd=[], rolling_rms=[], rolling_end_seconds=[])
        output["channels"][field] = channel
        if issues:
            continue
        frequencies, density = welch(values, fs=rate, window="hann", nperseg=nperseg,
                                     noverlap=nperseg // 2, detrend="constant", scaling="density")
        mask = (frequencies >= band_low) & (frequencies <= band_high)
        if not bool(mask.any()):
            raise VibrationInputError("所选频带内没有可用频率点，请扩大频带或增加记录长度。")
        selected_density = density[mask]
        channel["frequencies"] = frequencies[mask].tolist()
        channel["psd"] = selected_density.tolist()
        channel["band_rms"] = float(np.sqrt(np.sum(selected_density) * resolution))
        if dynamic_rms and float(np.max(selected_density)) > 0:
            channel["dominant_frequency_hz"] = float(frequencies[mask][int(np.argmax(selected_density))])
        window_size = (int(round(window_seconds * rate))
                       if window_seconds <= len(values) / rate else len(values) + 1)
        if 2 <= window_size <= len(values):
            sums = np.concatenate(([0.0], np.cumsum(centered * centered)))
            powers = np.maximum((sums[window_size:] - sums[:-window_size]) / window_size, 0)
            channel["rolling_rms"] = np.sqrt(powers).tolist()
            channel["rolling_end_seconds"] = offsets[window_size - 1:]
    return output
