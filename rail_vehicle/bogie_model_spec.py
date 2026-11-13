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


def model_contract():
    parts = {}

    def part(name, group, parent, origin, offset, spin=None, link=None):
        parts[name] = dict(group=group, parent=parent, origin=origin, offset=offset)
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
