"""Assembly contract for the generic two-axle demonstration bogie (metres, Z up)."""

from rail_vehicle.bogie_geometry import AXLE_X, AXLE_Z, BOX_Y, FRAME_Y, WHEEL_Y, connection_anchors

VERSION = 1
LAYERS = ("frame", "wheel", "fixed", "links")
DIMENSIONS = {
    "wheelbase": AXLE_X[1] - AXLE_X[0], "axle_height": AXLE_Z,
    "wheel_centres": 2 * WHEEL_Y, "tread_radius": .420, "flange_radius": .456,
    "axlebox_centres": 2 * BOX_Y, "side_beam_centres": 2 * FRAME_Y,
    "frame_length": 3.72, "side_beam_width": .235,
}
PART_LABELS = {
    "side_beam": "侧梁", "air_spring": "空气弹簧", "cross_members": "横梁组件",
    "reference_track": "轨道参照", "axle": "车轴", "motor": "牵引电机", "gearbox": "齿轮箱",
    "wheel": "车轮", "axlebox": "轴箱", "spring_seat_lower": "下弹簧座",
    "spring_seat_upper": "上弹簧座", "disc": "制动盘", "caliper": "制动卡钳",
    "coupling": "传动连接", "coil": "一系螺旋弹簧", "damper": "减振器",
    "guide": "导向件", "stay": "卡钳支撑",
}


def part_label(name):
    if name in PART_LABELS:
        return PART_LABELS[name]
    tokens = name.split("_")
    side = {"l": "左侧", "r": "右侧"}.get(tokens[-1])
    if side:
        tokens.pop()
    axle = {"a": "A 轴", "b": "B 轴"}.get(tokens[-1])
    if axle:
        tokens.pop()
    return " ".join(filter(None, (axle, side, PART_LABELS["_".join(tokens)])))


def model_contract():
    parts = {}

    def part(name, group, parent, origin, offset, spin=None, link=None):
        parts[name] = dict(label=part_label(name), group=group, parent=parent, origin=origin, offset=offset)
        if spin is not None:
            parts[name]["spin"] = spin
        if link is not None:
            parts[name]["link"] = link

    for suffix, side in (("l", -1), ("r", 1)):
        part(f"side_beam_{suffix}", "构架", "frame", (0, side*FRAME_Y, .98), (0, side*.14, .48))
        part(f"air_spring_{suffix}", "二系悬挂", "frame", (0, side*FRAME_Y, 1.14), (0, side*.12, .92))
    part("cross_members", "构架", "frame", (0, 0, 1.0), (0, 0, .48))
    part("reference_track", "轨道参照", "fixed", (0, 0, 0), (0, 0, 0))
    for end, x in zip(("a", "b"), AXLE_X):
        sign = 1 if x > 0 else -1
        spin = (x, 0, AXLE_Z)
        part(f"axle_{end}", "轮对", "wheel", spin, (sign*.28, 0, 0), spin)
        part(f"motor_{end}", "电机与传动", "frame", (x*.67, 0, .63), (-sign*.27, 0, .44))
        part(f"gearbox_{end}", "电机与传动", "wheel", (x, .43, .47), (sign*.25, .09, .31))
        for suffix, side in (("l", -1), ("r", 1)):
            key = f"{end}_{suffix}"
            part(f"wheel_{key}", "轮对", "wheel", (x, side*WHEEL_Y, AXLE_Z), (sign*.28, side*.10, 0), spin)
            part(f"axlebox_{key}", "轴箱", "wheel", (x, side*BOX_Y, AXLE_Z), (sign*.28, side*.46, .03))
            part(f"spring_seat_lower_{key}", "一系悬挂", "wheel", (x, side*BOX_Y, .625), (sign*.20, side*.30, .24))
            part(f"spring_seat_upper_{key}", "一系悬挂", "frame", (x, side*FRAME_Y, .826), (sign*.20, side*.30, .52))
            part(f"disc_{key}", "制动装置", "wheel", (x, side*.64, AXLE_Z), (sign*.43, side*.15, .12), spin)
            part(f"caliper_{key}", "制动装置", "wheel", (x, side*.64, .658), (sign*.43, side*.15, .34))
    for index, (group, kind, a, b) in enumerate(connection_anchors()):
        end = "a" if a[0] < 0 else "b"
        side = "l" if a[1] < 0 else "r"
        name = f"{kind}_{end}" if kind == "coupling" else f"{kind}_{end}_{side}"
        part(name, group, "links", a, ((-.2 if a[0] < 0 else .2), (-.38 if a[1] < 0 else .38), .38), link=index)
    return {
        "version": VERSION, "units": "m", "coordinates": "X longitudinal, Y transverse, Z up",
        "dimensions": DIMENSIONS, "parts": parts, "layers": LAYERS,
    }
