"""Generate the fictional demo CSVs and the Python-verified test baseline.

Run from the repository root with the project virtual environment:
    .venv/Scripts/python.exe qoder-sites-demo/tests/build_fixture.py

Reads the original rail_vehicle rules (read-only) and writes:
    qoder-sites-demo/assets/demo-run-1.csv
    qoder-sites-demo/assets/demo-run-2.csv
    qoder-sites-demo/tests/expected.json
Existing outputs are never overwritten; delete them manually to regenerate.
"""

import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rail_vehicle import demo_data
from rail_vehicle.anomaly import detect_anomalies_with_anchors
from rail_vehicle.bogie_replay import event_positions, gap_cutoff, replay_scene
from rail_vehicle.data_quality import summarize_timestamps
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.replay import prepare_replay
from rail_vehicle.run_data import validate_run_csv

DEMO_DIR = Path(__file__).resolve().parents[1]
THRESHOLD = 1.0


def rows_with_lines(content):
    vehicles = [{"vehicle_code": demo_data.DEMO_VEHICLE_CODE, "id": -1}]
    validation = validate_run_csv(content, vehicles)
    if not validation.is_valid:
        raise SystemExit("演示 CSV 未通过原项目校验：" + "; ".join(validation.errors))
    return validation


def batch_fixture(content, name):
    validation = rows_with_lines(content)
    rows = validation.rows
    analysis = analyze_run_data(rows, THRESHOLD)
    ordered = analysis["samples"]
    anomalies, anchors = detect_anomalies_with_anchors(ordered, THRESHOLD)
    track = prepare_replay(ordered)
    cutoff = gap_cutoff(track)
    positioned = event_positions(track, THRESHOLD)
    events = []
    for (event, start), anchor in zip(positioned, anchors):
        peak_row = next(i for i, row in enumerate(track.samples)
                        if row["timestamp"] == event["maximum_timestamp"]
                        and abs(row[event["field"]]) == event["maximum_absolute_value"])
        end_row = start + event["sample_count"] - 1
        scene = replay_scene(track, track.offsets[start], start, THRESHOLD, cutoff)
        events.append({
            **event,
            "start_row": start,
            "peak_row": peak_row,
            "end_row": end_row,
            "start_csv_line": track.samples[start]["csv_line"],
            "peak_csv_line": track.samples[peak_row]["csv_line"],
            "end_csv_line": track.samples[end_row]["csv_line"],
            "anchor": {"occurrence": anchor.occurrence, "group_size": anchor.group_size},
            "scene": {"label": scene.label, "mode": scene.mode, "parts": list(scene.parts)},
        })
    return {
        "file_name": name,
        "source_sha256": validation.source_sha256,
        "source_row_count": validation.source_row_count,
        "sampling_summary": summarize_timestamps(row["timestamp"] for row in rows),
        "analysis": {key: analysis[key] for key in
                     ("sample_count", "speed", "lateral_accel", "vertical_accel", "threshold")},
        "points": anomalies["points"],
        "events": events,
        "offsets": list(track.offsets),
        "gap_cutoff": cutoff,
        "csv_lines": [row["csv_line"] for row in track.samples],
    }


def edge_fixture():
    """Unsorted rows, duplicate timestamps and a strict-threshold boundary."""
    samples = [
        {"timestamp": "2026-10-08T09:30:03Z", "speed_kmh": 80.0, "lateral_accel": 1.0, "vertical_accel": 0.2, "csv_line": 4},
        {"timestamp": "2026-10-08T09:30:01Z", "speed_kmh": 78.0, "lateral_accel": -1.2, "vertical_accel": 0.1, "csv_line": 2},
        {"timestamp": "2026-10-08T09:30:02Z", "speed_kmh": 79.0, "lateral_accel": 1.5, "vertical_accel": -1.5, "csv_line": 3},
        {"timestamp": "2026-10-08T09:30:02Z", "speed_kmh": 79.5, "lateral_accel": 1.6, "vertical_accel": 0.3, "csv_line": 5},
        {"timestamp": "2026-10-08T09:30:04Z", "speed_kmh": 81.0, "lateral_accel": 0.4, "vertical_accel": 2.0, "csv_line": 6},
    ]
    analysis = analyze_run_data(samples, THRESHOLD)
    anomalies, _ = detect_anomalies_with_anchors(analysis["samples"], THRESHOLD)
    track = prepare_replay(analysis["samples"])
    positioned = event_positions(track, THRESHOLD)
    return {
        "input": samples,
        "ordered_timestamps": [row["timestamp"] for row in analysis["samples"]],
        "ordered_csv_lines": [row["csv_line"] for row in track.samples],
        "analysis": {key: analysis[key] for key in
                     ("sample_count", "speed", "lateral_accel", "vertical_accel", "threshold")},
        "points": anomalies["points"],
        "events": [{**event, "start_row": start} for event, start in positioned],
        "offsets": list(track.offsets),
        "sampling_summary": summarize_timestamps(row["timestamp"] for row in samples),
    }


def main():
    outputs = [DEMO_DIR / "assets" / "demo-run-1.csv", DEMO_DIR / "assets" / "demo-run-2.csv",
               DEMO_DIR / "tests" / "expected.json"]
    existing = [str(path) for path in outputs if path.exists()]
    if existing:
        raise SystemExit("输出已存在，为避免覆盖请先手动删除：" + ", ".join(existing))
    contents = [demo_data.demo_csv_content(0), demo_data.demo_csv_content(1)]
    fixture = {
        "threshold": THRESHOLD,
        "illustration_period": 2.4,
        "batches": [batch_fixture(content, name) for content, name in
                    zip(contents, ("demo-run-1.csv", "demo-run-2.csv"))],
        "edge": edge_fixture(),
    }
    for path, content in zip(outputs[:2], contents):
        path.write_text(content, encoding="utf-8", newline="")
    outputs[2].write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已生成：")
    for path in outputs:
        print(" ", path.relative_to(ROOT))


if __name__ == "__main__":
    main()