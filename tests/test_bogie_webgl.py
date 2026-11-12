"""Validate the model shipped to the offline renderer."""

import json
from math import dist, isfinite

from rail_vehicle.bogie_viewer import ASSETS, webgl_available
from rail_vehicle.bogie_geometry import AXLE_X, AXLE_Z, WHEEL_Y
from scripts.export_bogie_webgl import serialized_mesh


def test_offline_mesh_is_current_and_assembled_around_two_axles():
    assert webgl_available()
    text = (ASSETS / "assembly.json").read_text(encoding="utf-8")
    assert text == serialized_mesh()
    model = json.loads(text)
    wheels = {tuple(group): vertices for (part, group, parent, _), vertices, _, _ in model["mesh"]
              if part == "轮对" and parent == "wheel"}
    assert set(wheels) == {(x, side) for x in AXLE_X for side in (-1, 0, 1)}
    for (x, side), vertices in wheels.items():
        if side:
            assert max(dist((vx, vz), (x, AXLE_Z)) for vx, _, vz in vertices) < .457
            assert min(side*vy for _, vy, _ in vertices) > WHEEL_Y - .12
            assert min(vz for _, _, vz in vertices) < 0  # inner flange reaches below tread
        else:
            assert min(vy for _, vy, _ in vertices) < -WHEEL_Y
            assert max(vy for _, vy, _ in vertices) > WHEEL_Y
    for _, vertices, faces, tones in model["mesh"]:
        assert all(isfinite(value) for vertex in vertices for value in vertex)
        assert len(faces) == len(tones)
        assert all(len(set(face)) == 3 and min(face) >= 0 and max(face) < len(vertices) for face in faces)
    assert sum(link[1] == "coil" for link in model["links"]) == 4
