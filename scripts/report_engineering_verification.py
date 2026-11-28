"""Compile verification outcomes directly from a local pytest JUnit XML file."""

from argparse import ArgumentParser
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from xml.etree import ElementTree

from rail_vehicle.verification_matrix import SCENARIOS


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    raw = args.junit.read_bytes()
    tree = ElementTree.fromstring(raw)
    cases = {item.get("classname", "").removeprefix("tests.") + "." + item.get("name", ""): item
             for item in tree.iter("testcase")}
    rows = []
    for scenario in SCENARIOS:
        case = cases.get(scenario.test)
        if case is None:
            status = "missing"
        elif len(case) == 0:
            status = "pass"
        else:
            status = next((item.tag for item in case if item.tag in ("failure", "error", "skipped")), "pass")
        rows.append({**asdict(scenario), "status": status, "runtime_s": case.get("time") if case is not None else None})
    result = {"schema": "rail.engineering-verification.v1", "source_commit": args.commit,
              "junit_sha256": sha256(raw).hexdigest(), "scenarios": rows,
              "external_pending": ["独立 Windows x64 无 Python 设备", "独立实车、台架与接触基准", "真实云端模型"]}
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(f"Verification scenarios passed: {sum(row['status'] == 'pass' for row in rows)}/{len(rows)}")
    return int(any(row["status"] != "pass" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
