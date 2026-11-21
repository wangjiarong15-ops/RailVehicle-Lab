"""Regenerate the offline Three.js mesh from the shared teaching geometry."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rail_vehicle.bogie import PART_DESCRIPTIONS
from rail_vehicle.bogie_geometry import (
    AXLE_X, AXLE_Z, DAMPER_BODY_RATIO, DAMPER_ROD_RATIO, WHEEL_POSE_GAIN, WHEEL_Y, _rigid_template,
    connection_anchors,
)
from rail_vehicle.bogie_model_spec import model_contract

OUTPUT = Path(__file__).resolve().parents[1] / "rail_vehicle/assets/bogie_viewer/assembly.json"


def assembly_layout():
    return {
        "format": 1,
        "coordinates": "X longitudinal, Y transverse, Z up",
        "axleX": AXLE_X,
        "axleZ": AXLE_Z,
        "wheelY": WHEEL_Y,
        "wheelPoseGain": WHEEL_POSE_GAIN,
        "damperRatios": [DAMPER_BODY_RATIO, DAMPER_ROD_RATIO],
        "parts": PART_DESCRIPTIONS,
        "links": tuple(connection_anchors()),
    }


def mesh_document():
    return {
        **assembly_layout(),
        "mesh": [
            [key, [[round(value, 5) for value in vertex] for vertex in vertices], faces, tones]
            for key, vertices, faces, tones in _rigid_template()
        ],
    }


def serialized_mesh():
    return json.dumps(mesh_document(), ensure_ascii=False, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(serialized_mesh(), encoding="utf-8", newline="\n")
    OUTPUT.with_name("assembly-layout.json").write_text(
        json.dumps(assembly_layout(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    OUTPUT.with_name("model_contract.json").write_text(
        json.dumps(model_contract(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
