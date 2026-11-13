"""Check the exported GLB against the assembly contract without Blender."""

import argparse
import json
from math import dist, isfinite
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rail_vehicle.bogie_model_spec import model_contract

ASSETS = ROOT / "rail_vehicle/assets/bogie_viewer"
MAX_BYTES = 2_500_000
MAX_TRIANGLES = 48_000


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
    return json.loads(raw[20:start]), bin_size, len(raw)


def validate(path):
    doc, bin_size, size = read_glb(path)
    contract = model_contract()
    require(doc["asset"]["version"] == "2.0" and not doc.get("extensionsRequired"), "Requires base glTF 2.0")
    require(not any(doc.get(key) for key in ("images", "animations", "skins", "cameras")),
            "Expected self-contained PBR geometry with application-controlled animation")
    buffer, = doc["buffers"]
    require("uri" not in buffer and 0 <= bin_size-buffer["byteLength"] <= 3, "Expected one embedded buffer")
    for view in doc["bufferViews"]:
        require(view["buffer"] == 0 and 0 <= view.get("byteOffset", 0) <= buffer["byteLength"]-view["byteLength"],
                "Buffer view exceeds embedded geometry")
    nodes = doc["nodes"]
    root_index, = doc["scenes"][doc["scene"]]["nodes"]
    root = nodes[root_index]
    require(root["name"] == "rail_bogie" and root.get("extras") ==
            {"rail_contract": contract["version"], "rail_units": "m"}, "Wrong assembly root or units")
    layers = {nodes[i]["name"]: nodes[i] for i in root["children"]}
    require(set(layers) == {"rail_" + layer for layer in contract["layers"]}, "Wrong assembly layers")
    visited = {root_index, *root["children"]}
    expected = contract["parts"]
    ids, mesh_indices = set(), set()
    for layer, node in layers.items():
        require(not {"translation", "scale", "rotation", "matrix"}.intersection(node), "Layer must use scene origin")
        for index in node["children"]:
            item = nodes[index]
            name = item["name"]
            require(index not in visited and name in expected and name not in ids, f"Unexpected part {name}")
            spec = expected[name]
            visited.add(index)
            ids.add(name)
            require(layer == "rail_" + spec["parent"] and item.get("extras") ==
                    {"rail_part": name, "rail_group": spec["group"]}, f"Wrong parent or tags: {name}")
            x, up, depth = item.get("translation", (0,0,0))
            require(dist((x,-depth,up), spec["origin"]) < .0001, f"Invalid pivot: {name}")
            require(not {"matrix", "scale"}.intersection(item), f"Invalid part transform: {name}")
            require("rotation" not in item or spec["parent"] == "links", f"Rigid part must align with axes: {name}")
            surface, = item["children"]
            require(surface not in visited and "mesh" in nodes[surface] and not nodes[surface].get("children"),
                    f"Invalid surface: {name}")
            visited.add(surface)
            mesh_indices.add(nodes[surface]["mesh"])
    require(ids == set(expected) and len(visited) == len(nodes), "Incomplete assembly")
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
    return dict(parts=len(ids), meshes=len(mesh_indices), triangles=triangles, bytes=size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, default=ASSETS / "bogie.glb")
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.file)))
    except (ValueError, KeyError, IndexError, OSError, TypeError, struct.error) as exc:
        parser.exit(1, f"GLB validation failed: {exc}\n")
