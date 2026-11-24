"""Generate the shared fixture, or check the checked-in version for drift."""

from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path

from rail_vehicle.anomaly import detect_anomalies
from rail_vehicle.bogie_model_spec import model_contract
from rail_vehicle.bogie_replay import gap_cutoff, replay_scene
from rail_vehicle.dynamics import analyze_run_data
from rail_vehicle.replay import prepare_replay
from rail_vehicle.run_data import MAX_ABS_ACCELERATION, MAX_SPEED_KMH, REQUIRED_COLUMNS, validate_run_csv


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "phase8-v1.json"
HEADER = ",".join(REQUIRED_COLUMNS)
VEHICLES = [{"id": 1, "vehicle_code": "DEMO-001"}]
THRESHOLD = 1.0
CSV_CASES = {
    "ordinary": f"{HEADER}\n2026-10-08T09:30:00,DEMO-001,0,0.1,-0.2\n"
                "2026-10-08T09:30:01Z,DEMO-001,600,100,-100\n",
    "physical_lines_and_clock": f'\ufeff{HEADER},note\r\n'
                                '2026-10-08T09:30:20Z,DEMO-001,72,1.2,0,"one\r\ntwo"\r\n'
                                '\r\n2026-10-08T17:30:00+08:00,DEMO-001,72,1,-1,"a,b"\r\n'
                                '2026-10-08T09:30:01.000001Z,DEMO-001,73,1.0000000000000002,-1.2,"a""b"\r\n'
                                '2026-10-08T09:30:01.000001Z,DEMO-001,73,-1.2,1.2,duplicate\r\n'
                                '2026-10-08T09:30:02.000001Z,DEMO-001,73,0.9999999999999999,1,edge\r\n',
    "missing_and_nonfinite": f"{HEADER}\n2026-10-08T09:30:01Z,DEMO-001,,NaN,101\n",
    "outside_speed": f"{HEADER}\n2026-10-08T09:30:01Z,DEMO-001,601,0,0\n",
    "wrong_vehicle": f"{HEADER}\n2026-10-08T09:30:01Z,OTHER,10,0,0\n",
}


def case_result(content):
    validation = validate_run_csv(content.encode(), VEHICLES)
    result = dict(valid=validation.is_valid, source_row_count=validation.source_row_count,
                  missing_by_field=validation.missing_by_field, naive_timestamp_rows=validation.naive_timestamp_rows)
    if not validation.is_valid:
        return result
    rows = validation.rows
    analysis = analyze_run_data(rows, THRESHOLD)
    track = prepare_replay(rows)
    cutoff = gap_cutoff(track)
    middle = (track.offsets[-2] + track.offsets[-1]) / 2
    scene = replay_scene(track, middle, len(track.samples) - 2, THRESHOLD, cutoff)
    return {**result, "sampling": validation.sampling_summary, "sha256": validation.source_sha256,
            "analysis": {field: analysis[field] for field in ("sample_count", "speed", "lateral_accel", "vertical_accel", "threshold")},
            "anomalies": detect_anomalies(rows, THRESHOLD), "csv_lines": [row["csv_line"] for row in track.samples],
            "offsets": list(track.offsets), "gap_cutoff": cutoff, "midpoint_scene": scene.mode}


def build():
    model = model_contract()
    glb = ROOT / "rail_vehicle" / "assets" / "bogie_viewer" / "bogie.glb"
    return {"schema": "rail.phase8.cross-version.v1", "source_kind": "synthetic",
            "vehicle_codes": [vehicle["vehicle_code"] for vehicle in VEHICLES],
            "csv_contract": {"fields": list(REQUIRED_COLUMNS), "max_speed_kmh": MAX_SPEED_KMH,
                             "max_abs_acceleration_m_s2": MAX_ABS_ACCELERATION, "threshold_m_s2": THRESHOLD},
            "glb_sha256": sha256(glb.read_bytes()).hexdigest(), "assembly": model,
            "cases": {name: {"csv": csv, "expected": case_result(csv)} for name, csv in CSV_CASES.items()}}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate only the Phase 8 fixture")
    args = parser.parse_args()
    output = json.dumps(build(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(output, encoding="utf-8", newline="\n")
    elif FIXTURE.read_text(encoding="utf-8") != output:
        parser.error("Phase 8 fixture drifted. Review the contract before regenerating.")
    print(f"Phase 8 contract: {len(CSV_CASES)} CSV cases, {len(model_contract()['parts'])} model parts")


if __name__ == "__main__":
    main()
