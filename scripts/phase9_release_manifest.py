"""Create a reproducible inventory from committed source and verify frozen RC1."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ["qoder-sites-demo", "docs/qoder-competition"]
BASELINE = "95ccd2a02693550e8a9ad75b4ebe0f730410d675"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def inventory(revision, paths):
    result = {}
    for record in git("ls-tree", "-rz", revision, "--", *paths).split(b"\0"):
        if not record:
            continue
        fields, name = record.split(b"\t", 1)
        mode, kind, oid = fields.decode().split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError(f"Unsupported release entry: {name!r}")
        data = git("cat-file", "blob", oid)
        result[name.decode()] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if not result:
        raise ValueError("No release assets found")
    return result


def manifest(revision):
    commit = git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    frozen = inventory(BASELINE, FROZEN)
    if frozen != inventory(commit, FROZEN):
        raise ValueError("Frozen competition source or documentation differs from RC1")
    full = inventory(commit, ["rail_vehicle/assets/bogie_viewer", "pyproject.toml",
                              "app.py", "demo_app.py", "portable_launcher.py", "RailVehicleLab.spec"])
    for name, data in frozen.items():
        if name.endswith("bogie.glb"):
            if data != full["rail_vehicle/assets/bogie_viewer/bogie.glb"]:
                raise ValueError("The two GLB copies differ")
    return {"schema": "rail.release-source.v1", "build_id": f"phase9-{commit[:12]}",
            "full_commit": commit, "competition_commit": BASELINE,
            "full_assets": full, "competition_assets": frozen}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = manifest(args.revision)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(payload["build_id"], len(payload["full_assets"]), len(payload["competition_assets"]))


if __name__ == "__main__":
    main()
