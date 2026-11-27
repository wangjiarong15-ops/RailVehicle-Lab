"""Time alignment and read-only evidence for the whole-vehicle model."""

from dataclasses import dataclass
import csv
from hashlib import sha256
from io import StringIO
from math import isfinite

import numpy as np

from rail_vehicle.coupled_vehicle import MODEL, case_config, read_case, simulate
from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.sensor_store import active_channels, sensor_snapshot
from rail_vehicle.timestamp_utils import parse_iso_timestamp
from rail_vehicle.virtual_trials import json_text

SCHEMA = "rail.twin-observation.v1"
CHANNELS = {
    "speed": ("速度", "m/s", "vehicle.forward", .1, 100.),
    "body_y_accel": ("车体横向加速度", "m/s^2", "body.cg.+y", .002, 500.),
    "body_z_accel": ("车体垂向动态加速度", "m/s^2", "body.cg.+z.dynamic", .002, 500.),
    "body_roll": ("车体侧滚角", "rad", "body.roll.+y-up", .00005, 1.),
    "secondary_front_y": ("前转向架二系横向相对位移", "m", "secondary.front.body-minus-bogie.+y", .0001, .5),
    "secondary_rear_y": ("后转向架二系横向相对位移", "m", "secondary.rear.body-minus-bogie.+y", .0001, .5),
}
UNITS = {"m/s": {"m/s": 1., "km/h": 1/3.6}, "m/s^2": {"m/s^2": 1., "g": 9.80665},
         "rad": {"rad": 1., "mrad": .001, "deg": np.pi/180}, "m": {"m": 1., "mm": .001}}
SOURCES = {"demonstration": "虚构演示", "synthetic": "合成验证记录", "field_declared": "实测来源（用户声明）",
           "unknown": "来源未确认"}
COLUMNS = ("time_s", "channel", "value", "unit")


def digest(value):
    return sha256(json_text(value).encode()).hexdigest()


@dataclass(frozen=True)
class Series:
    time: tuple
    values: tuple
    lines: tuple
    metadata: dict


@dataclass(frozen=True)
class Observations:
    source: str
    vehicle_ref: str
    csv_sha256: str
    metadata_sha256: str
    record: str
    input_sha256: str | None
    excitation_record: str | None
    alignment_record: str
    channels: dict

    def evidence(self):
        return {"schema": SCHEMA, "source": self.source, "vehicle_ref":self.vehicle_ref,
                "record": self.record, "csv_sha256": self.csv_sha256,
                "metadata_sha256": self.metadata_sha256, "alignment_record": self.alignment_record,
                "excitation_record": self.excitation_record, "input_sha256": self.input_sha256,
                "channels": {key: data.metadata for key, data in self.channels.items()}}


def excitation_hash(case):
    """Do not include the three suspension coefficients being identified."""
    v = case_config(case)["case"]
    return digest({**{name: v[name] for name in ("track", "speed_m_s", "length_m", "lateral_m",
                                                   "vertical_m", "duration_s", "seed", "profile")},
                   "bogie_half_spacing_m":v["vehicle"]["bogie_half_spacing_m"]})


def _text(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 120


def _is_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def parse_observations(content, metadata):
    if not isinstance(content, bytes) or not 0 < len(content) <= 2_000_000:
        raise ValueError("观测 CSV 须在 2 MB 以内。")
    if not isinstance(metadata, dict) or metadata.get("schema") != SCHEMA:
        raise ValueError("观测元数据版本无效。")
    source, record, alignment = (metadata.get(k) for k in ("source", "record", "alignment_record"))
    vehicle=metadata.get("vehicle_ref")
    input_hash, excitation = metadata.get("input_sha256"), metadata.get("excitation_record")
    if not isinstance(source, str) or source not in SOURCES or not all(map(_text,(vehicle,record,alignment))):
        raise ValueError("请声明车辆、数据来源、记录和时基依据。")
    if source=="field_declared" and vehicle.startswith("EDIT-"):
        raise ValueError("请填写已核实的实测车辆编号。")
    if input_hash is not None and not _is_hash(input_hash):
        raise ValueError("激励摘要须为 SHA-256。")
    if excitation is not None and not _text(excitation):
        raise ValueError("激励依据无效。")
    bindings = metadata.get("channels")
    if not isinstance(bindings, dict) or not bindings or not set(bindings) <= CHANNELS.keys():
        raise ValueError("缺少有效的通道元数据。")
    streams = {}
    for key, entry in bindings.items():
        if not isinstance(entry, dict) or not _text(entry.get("sensor")):
            raise ValueError(f"{key} 缺少传感器编号。")
        if source=="field_declared" and entry["sensor"].startswith("EDIT-"):
            raise ValueError(f"{key} 须填写已核实的传感器编号。")
        version = entry.get("version")
        offset, scale = entry.get("offset_s"), entry.get("clock_scale")
        unit = entry.get("unit")
        if (type(version) is not int or version < 1 or type(offset) not in (int, float)
                or not -30 <= offset <= 30 or type(scale) not in (int, float) or not .995 <= scale <= 1.005
                or not isinstance(unit, str) or unit not in UNITS[CHANNELS[key][1]]
                or entry.get("mapping") != CHANNELS[key][2]):
            raise ValueError(f"{key} 的单位、安装映射、时钟参数或配置版本无效。")
        streams[key] = []
    try:
        reader = csv.reader(StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        if tuple(next(reader, ())) != COLUMNS:
            raise ValueError("表头应为 time_s,channel,value,unit。")
        for n, row in enumerate(reader, 1):
            if n > 15000:
                raise ValueError("每份观测记录最多 15000 行。")
            if not row:
                continue
            try:
                t, key, value, unit = row
                rule = bindings[key]
                if unit != rule["unit"]:
                    raise ValueError
                t = float(t)*rule["clock_scale"]+rule["offset_s"]
                value = float(value)*UNITS[CHANNELS[key][1]][unit]
                if not isfinite(t) or not -120 <= t <= 120 or not isfinite(value) or abs(value) > CHANNELS[key][-1]:
                    raise ValueError
                if key == "speed" and value < 0:
                    raise ValueError
            except (ValueError, KeyError, OverflowError):
                raise ValueError(f"CSV 第 {reader.line_num} 行的时间、通道、单位或数值无效。") from None
            streams[key].append((t, value, reader.line_num))
    except (UnicodeError, csv.Error):
        raise ValueError("请选择有效的 UTF-8 CSV。") from None
    series = {}
    for key, points in streams.items():
        if len(points) < 3 or any(b[0]-a[0] <= 1e-9 for a,b in zip(points, points[1:])):
            raise ValueError(f"{key} 至少需要 3 个唯一递增时刻。请复核重复、倒序或缺测通道。")
        times, values, lines = zip(*points, strict=True)
        info = {k: bindings[key][k] for k in ("sensor","version","unit","offset_s","clock_scale","mapping")}
        series[key] = Series(times, values, lines, info)
    return Observations(source,vehicle,sha256(content).hexdigest(), digest(metadata), record, input_hash,
                        excitation, alignment, series)


def quality(series):
    delta = np.diff(series.time)
    median = float(np.median(delta))
    return {"samples": len(series.time), "inferred_hz": 1/median, "median_step_s": median,
            "gaps": int(np.sum(delta > 2.5*median)), "jitter_fraction_p95": float(np.quantile(abs(delta/median-1), .95)),
            "window_s": [series.time[0], series.time[-1]]}


def model_channels(result):
    q, ddq, secondary = (np.asarray(result[k]) for k in ("q", "ddq", "secondary_y_m"))
    return {"speed": np.full(len(q), result["case"]["speed_m_s"]), "body_y_accel":ddq[:,10],
            "body_z_accel":ddq[:,0], "body_roll":q[:,11], "secondary_front_y":secondary[:,0],
            "secondary_rear_y":secondary[:,1]}


def check_case(observations, case):
    if observations.source == "unknown" or observations.input_sha256 != excitation_hash(case) or not observations.excitation_record:
        raise ValueError("需要可追溯的激励记录及与当前模型工况一致的激励摘要。未知来源可用于历史查看。")
    if "speed" not in observations.channels:
        raise ValueError("缺少同步速度通道。")
    speed = observations.channels["speed"]
    if speed.time[0] > .1 or speed.time[-1] < case.duration_s-.1 or quality(speed)["gaps"]:
        raise ValueError("同步速度通道未连续覆盖仿真窗。")
    if np.max(np.abs(np.asarray(speed.values)-case.speed_m_s)) > max(.3, .02*case.speed_m_s):
        raise ValueError("观测速度与匀速模型工况不一致。")


def residual_metrics(reference, calculated):
    err = np.asarray(calculated)-np.asarray(reference)
    return {"samples":len(err), "rmse":float(np.sqrt(np.mean(err**2))), "mae":float(np.mean(np.abs(err))),
            "peak_abs":float(np.max(np.abs(err))),
            "correlation": float(np.corrcoef(reference, calculated)[0,1])
            if len(err) > 2 and np.ptp(reference) and np.ptp(calculated) else None}


def compare(observations, result):
    if result.get("model") != MODEL:
        raise ValueError("当前结果缺少数字孪生模型版本。")
    case=read_case(json_text({k:result[k] for k in ("model","schema","parameter_source","case")}))
    check_case(observations,case)
    time = np.asarray(result["time_s"])
    values = model_channels(result)
    pairs = {}
    for key, series in observations.channels.items():
        stamps = np.asarray(series.time)
        keep = (stamps >= time[0]-1e-9) & (stamps <= time[-1]+1e-9)
        if np.count_nonzero(keep) < 3:
            continue
        obs = np.asarray(series.values)[keep]
        estimate = np.interp(stamps[keep], time, values[key])
        pairs[key] = {"time_s":stamps[keep].tolist(), "csv_lines":np.asarray(series.lines)[keep].tolist(),
                      "observed":obs.tolist(), "calculated":estimate.tolist(), "metrics":residual_metrics(obs, estimate)}
    return {"schema":"rail.twin-comparison.v1", "source":observations.evidence(),
            "model":MODEL, "simulation_evidence_id":result["evidence_id"],
            "configuration_sha256":result["configuration_sha256"], "alignment_method":"explicit clock, model interpolated to observation times",
            "quality":{key:quality(series) for key,series in observations.channels.items()}, "channels":pairs}


def fuse(time, observations, channels):
    """Join nearest observations to an explicit display grid without filling gaps."""
    time = np.asarray(time, dtype=float)
    joined = {}
    for key in channels:
        series = observations.channels[key]
        observed = np.asarray(series.time)
        after = np.clip(np.searchsorted(observed,time),0,len(observed)-1)
        before = np.clip(after-1,0,len(observed)-1)
        choice = np.where(abs(observed[after]-time) < abs(observed[before]-time),after,before)
        distance = abs(observed[choice]-time)
        valid = ((time >= observed[0]-1e-8) & (time <= observed[-1]+1e-8)
                 & (distance <= .51*quality(series)["median_step_s"]))
        joined[key] = [{"value":series.values[i],"csv_line":series.lines[i],"observed_time_s":series.time[i]}
                       if keep else None for keep,i in zip(valid,choice,strict=True)]
    return joined


def historic_samples(vehicle_id, batch_id, db_path=DB_PATH):
    """Use original CSV values for history, with no inferred vehicle motion."""
    with get_connection(db_path) as db:
        rows = [dict(row) for row in db.execute(
            "SELECT id,timestamp,speed_kmh,lateral_accel,vertical_accel,csv_line FROM run_samples "
            "WHERE vehicle_id=? AND batch_id=? ORDER BY timestamp,id LIMIT 6001", (vehicle_id,batch_id))]
    if len(rows) < 3:
        raise ValueError("历史批次至少需要 3 条观测。")
    truncated = len(rows) > 6000
    rows = rows[:6000]
    times = [parse_iso_timestamp(row["timestamp"]) for row in rows]
    if any(t is None for t in times):
        raise ValueError("含旧格式无效时间戳，请在运行分析查验。")
    stamps = tuple((t-times[0]).total_seconds() for t in times)
    if any(b < a for a,b in zip(stamps,stamps[1:])):
        raise ValueError("历史时间顺序需先复核。")
    distinct = len(set(stamps))
    gaps = None if distinct < 3 else quality(Series(tuple(sorted(set(stamps))),(),(),{}))
    snapshot = sensor_snapshot(vehicle_id,batch_id,db_path)
    return {"vehicle_id":vehicle_id,"batch_id":batch_id,"csv_sha256":snapshot.get("source_sha256"),
            "source":snapshot.get("source_kind") or "unknown","record":snapshot.get("file_name"),
            "clock_origin_utc":times[0].isoformat(),"time_s":stamps,"duplicate_timestamps":len(stamps)-distinct,
            "quality":gaps,"truncated":truncated,"samples":rows,
            "sensors":{key:{"version":entry["version"],"serial":entry["serial"],"metadata":entry["metadata"]}
                       for key,entry in active_channels(snapshot).items()}}


def observation_template(case, *, record="教学记录", source="unknown", injected_channels=None):
    keys = tuple(injected_channels or CHANNELS)
    return {"schema":SCHEMA, "source":source, "record":record,
            "vehicle_ref":"虚构教学车" if source=="demonstration" else "EDIT-VEHICLE",
            "alignment_record":"共同触发时标与传感器偏移的记录编号",
            "excitation_record":None, "input_sha256":excitation_hash(case),
            "channels":{key:{"sensor":f"EDIT-{key}", "version":1, "mapping":CHANNELS[key][2],
                             "offset_s":0., "clock_scale":1., "unit":CHANNELS[key][1]} for key in keys}}


def synthetic_observations(case, *, rate, seed, noise=0., clock_offset=0., clock_scale=1.):
    """Use refined integration and asynchronous sample grids for synthetic validation."""
    result = simulate(case, substeps=20, include_modes=False)
    keys = ("speed","body_y_accel","body_roll","secondary_front_y")
    metadata = observation_template(case, record=f"虚构观测-{seed}", source="demonstration", injected_channels=keys)
    metadata.update(excitation_record="由已知教学输入产生", alignment_record="合成时钟带已知独立偏移和速率")
    rng = np.random.default_rng(seed)
    buffer = StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    values = model_channels(result)
    for i,key in enumerate(keys):
        binding = metadata["channels"][key]
        binding.update(offset_s=clock_offset*i, clock_scale=clock_scale)
        stamps = np.r_[np.arange(i/(rate*4), case.duration_s, 1/rate), float(case.duration_s)]
        stamps = np.unique(stamps)
        sample = np.interp(stamps, result["time_s"], values[key])
        if key != "speed":
            sample += rng.normal(0, noise*max(np.std(sample), CHANNELS[key][3]), len(sample))
        writer.writerows(((t-binding["offset_s"])/clock_scale, key, v, binding["unit"]) for t,v in zip(stamps,sample,strict=True))
    data = buffer.getvalue().encode("utf8")
    return parse_observations(data, metadata), data, metadata
