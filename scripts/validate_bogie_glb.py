"""Check the exported GLB against the assembly contract without Blender."""

import argparse
from dataclasses import dataclass
import json
from math import cos, dist, hypot, isfinite, sin, tau
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rail_vehicle.bogie_geometry import (
    DAMPER_BODY_RATIO, DAMPER_ROD_RATIO, WHEEL_POSE_GAIN, connection_anchors, motion,
)
from rail_vehicle.bogie_model_spec import model_contract
from rail_vehicle.bogie_replay import TEACHING

ASSETS = ROOT / "rail_vehicle/assets/bogie_viewer"
MAX_BYTES = 2_500_000
MAX_TRIANGLES = 48_000
CONTACT_TOLERANCE_M = .001
ANIMATION_CLEARANCE_M = .002
ANIMATION_SAMPLES = 16
DAMPER_INSERTION_M = .010
TRANSFORM_KEYS = {"translation", "scale", "rotation", "matrix"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_glb(path):
    raw = Path(path).read_bytes()
    require(28 <= len(raw) <= MAX_BYTES, "GLB outside the size budget")
    require(struct.unpack_from("<4sII", raw) == (b"glTF", 2, len(raw)), "Invalid GLB header")
    json_size, json_type = struct.unpack_from("<I4s", raw, 12)
    start = 20 + json_size
    require(json_type == b"JSON" and json_size % 4 == 0 and start + 8 <= len(raw), "Invalid JSON chunk")
    bin_size, bin_type = struct.unpack_from("<I4s", raw, start)
    require(bin_type == b"BIN\0" and bin_size % 4 == 0 and start+8+bin_size == len(raw),
            "Invalid BIN chunk")
    return json.loads(raw[20:start]), memoryview(raw)[start + 8:], len(raw)


def _read_geometry_accessor(doc, binary, index, components):
    accessor = doc["accessors"][index]
    require("sparse" not in accessor, "Sparse assembly geometry is unsupported")
    format_code = components.get(accessor["componentType"])
    require(format_code is not None, "Invalid assembly geometry component type")
    record = struct.Struct("<" + format_code)
    view = doc["bufferViews"][accessor["bufferView"]]
    stride = view.get("byteStride", record.size)
    offset = accessor.get("byteOffset", 0)
    count = accessor["count"]
    require(stride >= record.size and 0 <= offset and count > 0 and
            offset + stride * (count - 1) + record.size <= view["byteLength"], "Geometry exceeds its buffer view")
    offset += view.get("byteOffset", 0)
    return [record.unpack_from(binary, offset + i * stride) for i in range(count)]


def _local_triangles(doc, binary, surface):
    for primitive in doc["meshes"][surface["mesh"]]["primitives"]:
        positions = _read_geometry_accessor(doc, binary, primitive["attributes"]["POSITION"], {5126: "fff"})
        require(all(isfinite(v) for point in positions for v in point), f"Non-finite vertex: {surface['name']}")
        indices = [item[0] for item in _read_geometry_accessor(
            doc, binary, primitive["indices"], {5121: "B", 5123: "H", 5125: "I"})]
        require(max(indices) < len(positions), f"Index outside mesh: {surface['name']}")
        for index in range(0, len(indices), 3):
            yield tuple(positions[i] for i in indices[index:index + 3])


def _rest_triangles(doc, binary, part, surface):
    x, up, depth = part.get("translation", (0, 0, 0))
    for face in _local_triangles(doc, binary, surface):
        yield tuple((x + px, -depth - pz, up + py) for px, py, pz in face)


def _plane_points(faces, axis, coordinate):
    """Yield the corners of each triangle/plane intersection in model metres."""
    for face in faces:
        if not face:
            continue
        for a, b in zip(face, (*face[1:], face[0])):
            delta_a, delta_b = a[axis] - coordinate, b[axis] - coordinate
            if abs(delta_a) < 1e-7:
                yield a
            if delta_a * delta_b < 0:
                fraction = delta_a / (delta_a - delta_b)
                yield tuple(u + fraction * (v - u) for u, v in zip(a, b))


def _vertical_slice(faces, x, y):
    sections = (tuple(_plane_points((face,), 0, x)) for face in faces)
    return (p[2] for p in _plane_points(sections, 1, y))


def _validate_wheel_track_fit(doc, binary, parts, contract):
    """Check nominal pose and spin envelope of the illustration, not wheel/rail mechanics."""
    track = tuple(_rest_triangles(doc, binary, *parts["reference_track"]))
    track_stations = {p[1] for face in track for p in face}
    tread_height = contract["dimensions"]["axle_height"] - contract["dimensions"]["tread_radius"]
    for name in ("wheel_a_l", "wheel_a_r", "wheel_b_l", "wheel_b_r"):
        wheel = tuple(_rest_triangles(doc, binary, *parts[name]))
        wheel_stations = {p[1] for face in wheel for p in face}
        x, y, z = contract["parts"][name]["origin"]
        top = max(_vertical_slice(track, x, y), default=-1.)
        tread = min(_vertical_slice(wheel, x, y), default=1.)
        require(abs(top - tread_height) <= CONTACT_TOLERANCE_M and abs(tread - top) <= CONTACT_TOLERANCE_M,
                f"Nominal tread and rail top do not align: {name}")
        side = 1 if y > 0 else -1
        inner_flange = [p[2] for face in wheel for p in face if side * (p[1] - y) < 0]
        require(inner_flange and min(inner_flange) < top - .02, f"Missing inboard flange: {name}")
        # A triangle's slice endpoints move linearly between its vertex stations.
        # Maximum rail height and radius are convex there, so check their sum at
        # interval ends. Taking the highest rail across X bounds any spin about Y.
        first, last = min(wheel_stations), max(wheel_stations)
        stations = wheel_stations | {station for station in track_stations if first <= station <= last}
        for station in stations:
            rail = max((p[2] for p in _plane_points(track, 1, station)), default=-1.)
            radius = max((hypot(px - x, pz - z) for px, _, pz in _plane_points(wheel, 1, station)), default=0.)
            require(rail <= z - radius + CONTACT_TOLERANCE_M, f"Wheel spin envelope intersects rail: {name}")


def _oriented_area(a, b, point):
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])


def _rod_axis_distance(face, start):
    """Closest radial distance from the axis to the surface above the deepest rod position."""
    polygon = []
    for a, b in zip(face, (*face[1:], face[0])):
        if a[0] >= start:
            polygon.append(a[1:])
        if (a[0] - start) * (b[0] - start) < 0:
            ratio = (start - a[0]) / (b[0] - a[0])
            polygon.append(tuple(u + ratio * (v-u) for u, v in zip(a[1:], b[1:])))
    if not polygon:
        return float("inf")
    edges = tuple(zip(polygon, (*polygon[1:], polygon[0])))
    signs = [_oriented_area(a, b, (0, 0)) for a, b in edges]
    if abs(sum(signs)) > 1e-12 and (min(signs) >= 0 or max(signs) <= 0):
        return 0.  # A face across the bore can cover the axis without any vertex at its centre.
    closest = float("inf")
    for (x, y), (u, v) in edges:
        dx, dy = u-x, v-y
        norm2 = dx*dx + dy*dy
        t = max(0., min(1., -(x*dx + y*dy) / norm2)) if norm2 else 0.
        closest = min(closest, hypot(x + t*dx, y + t*dy))
    return closest


def _joint_position(point, lateral, vertical, yaw):
    x, y, z = point
    cy, sy = cos(yaw), sin(yaw)
    return x * cy - y * sy, x * sy + y * cy + lateral, z + vertical


def _validate_dampers(doc, binary, dampers, contract):
    """Check the two rigid surfaces over sampled illustration poses, not physical damper travel."""
    anchors = tuple(connection_anchors())
    offsets = tuple(motion(mode, step * tau / ANIMATION_SAMPLES)
                    for mode in dict.fromkeys(scene.mode for scene in TEACHING.values())
                    for step in range(ANIMATION_SAMPLES))
    for name, (part, segments) in dampers.items():
        _, kind, a, b = anchors[contract["parts"][name]["link"]]
        rest = dist(a, b)
        direction = tuple((end-start) / rest for start, end in zip(a, b))
        x, y, z, w = part.get("rotation", (0, 0, 0, 1))
        # glTF axes are Y up; the local sliding axis (+Y in Blender) is -Z here.
        gltf_axis = (-2*(x*z + w*y), 2*(w*x - y*z), -1 + 2*(x*x + y*y))
        require(kind == "damper" and abs(hypot(x, y, z, w) - 1.) < 1e-5 and
                dist((gltf_axis[0], -gltf_axis[2], gltf_axis[1]), direction) < .0001,
                f"Damper sliding axis does not connect its pivots: {name}")

        def axial_triangles(segment):
            return tuple(tuple((-pz, px, py) for px, py, pz in face)
                         for face in _local_triangles(doc, binary, segments[segment]))

        body, rod = axial_triangles("body"), axial_triangles("rod")
        body_stations = [p[0] for triangle in body for p in triangle]
        rod_points = [p for triangle in rod for p in triangle]
        mouth = max(body_stations)
        rod_tip, rod_end = min(p[0] for p in rod_points), max(p[0] for p in rod_points)
        require(abs(min(body_stations)) <= CONTACT_TOLERANCE_M and
                abs(mouth - rest * DAMPER_BODY_RATIO) <= CONTACT_TOLERANCE_M and
                abs(rod_end - rest) <= CONTACT_TOLERANCE_M and
                abs(rod_tip - rest * (1 - DAMPER_ROD_RATIO)) <= CONTACT_TOLERANCE_M,
                f"Damper surfaces miss a nominal pivot or length: {name}")
        tip_positions = []
        for lateral, vertical, yaw in offsets:
            lower = _joint_position(a, lateral * WHEEL_POSE_GAIN, 0., yaw * WHEEL_POSE_GAIN)
            upper = _joint_position(b, lateral, vertical, yaw)
            tip_positions.append(rod_tip + dist(lower, upper) - rest)
        require(mouth - max(tip_positions) >= DAMPER_INSERTION_M, f"Damper rod leaves its bore: {name}")
        radius = max(hypot(px, py) for _, px, py in rod_points)
        clearance = min(_rod_axis_distance(face, min(tip_positions)) for face in body) - radius
        require(clearance >= ANIMATION_CLEARANCE_M, f"Damper rod intersects its bore or bottom stop: {name}")


@dataclass(frozen=True)
class _ProjectedFace:
    points: tuple
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    area: float

    def height(self, point):
        a, b, c = self.points
        return (_oriented_area(b, c, point) * a[2] + _oriented_area(c, a, point) * b[2] +
                _oriented_area(a, b, point) * c[2]) / self.area


def _projected_faces(faces):
    for a, b, c in faces:
        area = _oriented_area(a, b, c)
        if abs(area) < 1e-10:  # Vertical faces have no interior in the horizontal projection.
            continue
        points = (a, b, c) if area > 0 else (a, c, b)
        x, y, z = zip(*points)
        yield _ProjectedFace(points, min(x), max(x), min(y), max(y), min(z), max(z), abs(area))


def _overlap_polygon(lower, upper):
    """Clip the horizontal projection of one triangle against the other."""
    polygon = [p[:2] for p in lower.points]
    for a, b in zip(upper.points, (*upper.points[1:], upper.points[0])):
        if not polygon:
            break
        previous = polygon[-1]
        clipped = []
        for point in polygon:
            last = _oriented_area(a, b, previous)
            current = _oriented_area(a, b, point)
            if (last >= 0) != (current >= 0):
                fraction = last / (last - current)
                clipped.append(tuple(u + fraction * (v - u) for u, v in zip(previous, point)))
            if current >= 0:
                clipped.append(point)
            previous = point
        polygon = clipped
    return polygon


def _has_clearance(upper, lower):
    if (min(face.min_z for face in upper) - max(face.max_z for face in lower) >= ANIMATION_CLEARANCE_M or
            max(face.max_x for face in upper) < min(face.min_x for face in lower) or
            max(face.max_x for face in lower) < min(face.min_x for face in upper) or
            max(face.max_y for face in upper) < min(face.min_y for face in lower) or
            max(face.max_y for face in lower) < min(face.min_y for face in upper)):
        return True
    for top in upper:
        for bottom in lower:
            if (top.min_z - bottom.max_z >= ANIMATION_CLEARANCE_M or
                    top.max_x < bottom.min_x or bottom.max_x < top.min_x or
                    top.max_y < bottom.min_y or bottom.max_y < top.min_y):
                continue
            # The height difference is linear on this overlap. Its minimum is
            # attained at a polygon corner, including projected edge crossings.
            if any(top.height(p) - bottom.height(p) < ANIMATION_CLEARANCE_M
                   for p in _overlap_polygon(bottom, top)):
                return False
    return True


def _posed_faces(faces, lateral, vertical, yaw, *, spin=None, phase=0.):
    cy, sy = cos(yaw), sin(yaw)
    cs, ss = cos(phase), sin(phase)

    def move(point):
        x, y, z = point
        if spin is not None:
            x0, _, z0 = spin
            dx, dz = x - x0, z - z0
            x, z = x0 + dx * cs + dz * ss, z0 - dx * ss + dz * cs
        return x * cy - y * sy, x * sy + y * cy + lateral, z + vertical

    posed = {point: move(point) for point in {point for face in faces for point in face}}
    return tuple(_projected_faces(tuple(posed[point] for point in face) for face in faces))


def _validate_animation_clearance(doc, binary, parts, contract):
    """Sample steady-state assembled scenes for selected vertical clearances."""
    pairs = [("cross_members", f"wheel_{axle}_{side}") for axle in "ab" for side in "lr"]
    pairs += [(f"side_beam_{side}", f"axlebox_{axle}_{side}") for axle in "ab" for side in "lr"]
    names = {name for pair in pairs for name in pair}
    meshes = {name: tuple(_rest_triangles(doc, binary, *parts[name])) for name in names}
    spec = contract["parts"]
    for mode in dict.fromkeys(scene.mode for scene in TEACHING.values()):
        for step in range(ANIMATION_SAMPLES):
            phase = step * tau / ANIMATION_SAMPLES
            lateral, vertical, yaw = motion(mode, phase)
            posed = {}
            for name, faces in meshes.items():
                offsets = (lateral, vertical, yaw) if spec[name]["parent"] == "frame" else (
                    lateral * WHEEL_POSE_GAIN, 0., yaw * WHEEL_POSE_GAIN)
                spin = spec[name].get("spin")
                posed[name] = _posed_faces(faces, *offsets, spin=spin, phase=phase)
            for upper, lower in pairs:
                require(_has_clearance(posed[upper], posed[lower]),
                        f"Animation clearance too small: {upper}/{lower}, {mode}, {step * 360 / ANIMATION_SAMPLES:g}°")


def validate(path):
    doc, binary, size = read_glb(path)
    contract = model_contract()
    require(doc["asset"]["version"] == "2.0" and not doc.get("extensionsRequired"), "Requires base glTF 2.0")
    require(not any(doc.get(key) for key in ("images", "animations", "skins", "cameras")),
            "Expected self-contained PBR geometry with application-controlled animation")
    buffer, = doc["buffers"]
    require("uri" not in buffer and 0 <= len(binary)-buffer["byteLength"] <= 3, "Expected one embedded buffer")
    for view in doc["bufferViews"]:
        require(view["buffer"] == 0 and 0 <= view.get("byteOffset", 0) <= buffer["byteLength"]-view["byteLength"],
                "Buffer view exceeds embedded geometry")
    nodes = doc["nodes"]
    root_index, = doc["scenes"][doc["scene"]]["nodes"]
    root = nodes[root_index]
    require(root["name"] == "rail_bogie" and root.get("extras") ==
            {"rail_contract": contract["version"], "rail_units": "m"}, "Wrong assembly root or units")
    require(not TRANSFORM_KEYS.intersection(root), "Assembly root must use scene origin")
    layers = {nodes[i]["name"]: nodes[i] for i in root["children"]}
    require(set(layers) == {"rail_" + layer for layer in contract["layers"]}, "Wrong assembly layers")
    visited = {root_index, *root["children"]}
    expected = contract["parts"]
    parts, dampers, mesh_indices = {}, {}, set()
    for layer, node in layers.items():
        require(not TRANSFORM_KEYS.intersection(node), "Layer must use scene origin")
        for index in node["children"]:
            item = nodes[index]
            name = item["name"]
            require(index not in visited and name in expected and name not in parts and name not in dampers,
                    f"Unexpected part {name}")
            spec = expected[name]
            visited.add(index)
            require(layer == "rail_" + spec["parent"] and item.get("extras") ==
                    {"rail_part": name, "rail_group": spec["group"]}, f"Wrong parent or tags: {name}")
            x, up, depth = item.get("translation", (0,0,0))
            require(dist((x,-depth,up), spec["origin"]) < .0001, f"Invalid pivot: {name}")
            require(not {"matrix", "scale"}.intersection(item), f"Invalid part transform: {name}")
            require("rotation" not in item or spec["parent"] == "links", f"Rigid part must align with axes: {name}")
            is_damper = name.startswith("damper_")
            children = item["children"]
            require(len(children) == (2 if is_damper else 1), f"Invalid surface count: {name}")
            surfaces = {}
            for child in children:
                surface = nodes[child]
                require(child not in visited and "mesh" in surface and not surface.get("children") and
                        not TRANSFORM_KEYS.intersection(surface), f"Invalid surface or transform: {name}")
                visited.add(child)
                require(surface["mesh"] not in mesh_indices, f"Shared surface mesh: {name}")
                mesh_indices.add(surface["mesh"])
                role = surface.get("extras", {}).get("rail_damper_segment")
                valid_role = role in ("body", "rod") and role not in surfaces if is_damper else role is None
                require(valid_role, f"Invalid damper surface tag: {name}")
                surfaces[role] = surface
            if is_damper:
                dampers[name] = item, surfaces
            else:
                parts[name] = item, surfaces[None]
    require(parts.keys() | dampers.keys() == expected.keys() and len(visited) == len(nodes), "Incomplete assembly")
    require(mesh_indices == set(range(len(doc["meshes"]))), "Orphan mesh")
    triangles = 0
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            require(primitive.get("mode", 4) == 4 and 0 <= primitive["material"] < len(doc["materials"]),
                    "Expected triangles with PBR material")
            attributes = primitive["attributes"]
            position = doc["accessors"][attributes["POSITION"]]
            normal = doc["accessors"][attributes["NORMAL"]]
            indices = doc["accessors"][primitive["indices"]]
            require(position["type"] == normal["type"] == "VEC3" and position["count"] == normal["count"] > 0,
                    "Invalid mesh positions/normals")
            require(all(isfinite(v) and abs(v) < 5 for v in position["min"]+position["max"]), "Invalid mesh bounds")
            require(indices["type"] == "SCALAR" and indices["count"] > 0 and indices["count"] % 3 == 0,
                    "Invalid triangle index count")
            triangles += indices["count"]//3
    require(0 < triangles <= MAX_TRIANGLES, "Model exceeds triangle budget")
    _validate_dampers(doc, binary, dampers, contract)
    _validate_wheel_track_fit(doc, binary, parts, contract)
    _validate_animation_clearance(doc, binary, parts, contract)
    return dict(parts=len(parts) + len(dampers), meshes=len(mesh_indices), triangles=triangles, bytes=size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, default=ASSETS / "bogie.glb")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.file)))
    except (ValueError, KeyError, IndexError, OSError, TypeError, struct.error) as exc:
        parser.exit(1, f"GLB validation failed: {exc}\n")
