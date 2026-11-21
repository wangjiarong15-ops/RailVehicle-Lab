"""Single-axis accelerometer registration. Vectors are columns; lengths are metres."""

from datetime import datetime, timedelta, timezone
from math import fsum, isfinite

from rail_vehicle.bogie_model_spec import model_contract


CHANNELS = {"lateral_accel": "横向加速度", "vertical_accel": "垂向加速度"}
PARTS = model_contract()["parts"]
UNITS = {"m/s²": 1., "g": 9.80665, "mm/s²": .001}
AXES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
STATES = ("unknown", "unverified", "verified")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("参数必须为有限数值。")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError("参数超出支持的数值范围。") from None
    if not isfinite(value):
        raise ValueError("参数必须为有限数值。")
    return value


def vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("坐标或方向必须包含三个数值。")
    return [number(v) for v in value]


def dot(a, b):
    return fsum(x*y for x, y in zip(a, b, strict=True))


def rotation(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("旋转矩阵需要三行三列。")
    a, b, c = rows = [vector(row) for row in value]
    if any(abs(x) > 1.000001 for row in rows for x in row):
        raise ValueError("旋转矩阵必须正交。")
    if any(abs(dot(r, s) - (i == j)) > 1e-6
           for i, r in enumerate(rows) for j, s in enumerate(rows)):
        raise ValueError("旋转矩阵必须正交。")
    det = dot(a, [b[1]*c[2]-b[2]*c[1], b[2]*c[0]-b[0]*c[2], b[0]*c[1]-b[1]*c[0]])
    if abs(det - 1.) > 1e-6:
        raise ValueError("右手旋转矩阵的行列式必须为 +1。")
    return rows


def transform_direction(matrix, direction):
    direction = vector(direction)
    return [dot(row, direction) for row in rotation(matrix)]


def transform_point(matrix, translation_m, point_m):
    return [number(x+y) for x, y in zip(transform_direction(matrix, point_m), vector(translation_m), strict=True)]


def acceleration_si(value, unit, gain, bias):
    if unit not in UNITS:
        raise ValueError("未知加速度单位。")
    if number(gain) <= 0:
        raise ValueError("增益必须大于零，反向请用测量轴正方向表示。")
    return number((number(value)*gain + number(bias))*UNITS[unit])


def validate_metadata(values):
    """Validate declarations without supplying any physical zero or identity transform."""
    options = {"position_frame": ("model", "vehicle"), "axis": AXES, "unit": tuple(UNITS),
               "calibration": STATES, "mount_status": STATES, "clock_status": STATES, "time_basis": ("utc", "device_offset"),
               "origin": ("recorded", "illustration")}
    numbers = ("gain", "bias", "rate_hz", "clock_offset_s")
    vectors = ("position_m", "vehicle_origin_model_m")
    matrices = ("sensor_to_vehicle", "vehicle_to_model")
    if not isinstance(values, dict) or set(values) - {*options, *numbers, *vectors, *matrices, "source", "part_id"}:
        raise ValueError("配置中包含未定义字段。")
    m = {k: v for k, v in values.items() if v is not None and v != ""}
    if any(k in m and m[k] not in allowed for k, allowed in options.items()):
        raise ValueError("配置状态、坐标系、单位或轴向无效。")
    if "part_id" in m and (not isinstance(m["part_id"], str) or m["part_id"] not in PARTS):
        raise ValueError("部件 ID 不在当前 GLB 装配清单内。")
    if "source" in m and (not isinstance(m["source"], str) or not m["source"].strip() or len(m["source"]) > 200):
        raise ValueError("来源须为 1 至 200 个字符。")
    for key in numbers:
        if key in m:
            m[key] = number(m[key])
    if any(k in m and m[k] <= 0 for k in ("rate_hz", "gain")):
        raise ValueError("采样率和增益必须为正数。")
    if m.get("time_basis") != "device_offset" and "clock_offset_s" in m:
        raise ValueError("时钟偏移仅适用于已声明的设备时间基准。")
    if m.get("clock_status") == "verified" and (
        {"source", "origin", "time_basis"} - m.keys() or
        m["time_basis"] == "device_offset" and "clock_offset_s" not in m
    ):
        raise ValueError("核验时钟需要时间基准、来源和适用的时钟偏移。")
    for keys, parse in ((vectors, vector), (matrices, rotation)):
        for key in keys:
            if key in m:
                m[key] = parse(m[key])
    if m.get("mount_status") == "verified":
        required = {"part_id", "position_frame", "position_m", "origin", "source"}
        if m.get("position_frame") == "vehicle":
            required.update(("vehicle_to_model", "vehicle_origin_model_m"))
        if required - m.keys():
            raise ValueError("核验安装点需要部件、坐标系、位置、来源及适用的车辆到模型变换。")
    if m.get("calibration") == "verified":
        if {"axis","unit","gain","bias","sensor_to_vehicle","source","origin"} - m.keys():
            raise ValueError("已标定通道需要轴向、单位、增益、偏置、姿态及来源。")
        if m["unit"] != "m/s²":
            raise ValueError("现有 CSV 加速度列约定为 m/s²，请先核实或换算源文件单位。")
        from rail_vehicle.run_data import MAX_ABS_ACCELERATION
        for limit in [-MAX_ABS_ACCELERATION, MAX_ABS_ACCELERATION]:
            acceleration_si(limit, m["unit"], m["gain"], m["bias"])
    try:
        registration(m)
    except OverflowError:
        raise ValueError("空间变换超出数值范围。") from None
    return m


def registration(m):
    position = direction = None
    if m.get("mount_status") == "verified":
        position = (m["position_m"] if m["position_frame"] == "model" else
                    transform_point(m["vehicle_to_model"], m["vehicle_origin_model_m"], m["position_m"]))
    vehicle_direction = None
    if m.get("calibration") == "verified":
        axis = m["axis"]
        local = [float((1 if axis[0] == "+" else -1) if name == axis[1] else 0) for name in "XYZ"]
        vehicle_direction = transform_direction(m["sensor_to_vehicle"], local)
        if "vehicle_to_model" in m:
            direction = transform_direction(m["vehicle_to_model"], vehicle_direction)
    return dict(position=position, direction=direction, vehicle_direction=vehicle_direction)


def aligned_time(raw, metadata):
    """Only return a synchronized instant when the source clock convention is declared."""
    basis = metadata.get("time_basis")
    if metadata.get("clock_status") != "verified" or not raw or not basis or basis == "device_offset" and "clock_offset_s" not in metadata:
        return None
    try:
        instant = datetime.fromisoformat(raw)
        instant = instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)
        if basis == "device_offset":
            instant += timedelta(seconds=metadata["clock_offset_s"])
        return instant.isoformat()
    except (ValueError, TypeError, OverflowError):
        return None
