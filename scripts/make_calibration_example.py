"""Write a deterministic pair of synthetic calibration-check CSV files."""

from argparse import ArgumentParser
import csv
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path

from rail_vehicle.calibration_check import REFERENCE_COLUMNS
from rail_vehicle.demo_data import CSV_HEADER


VEHICLE_CODE = "CAL-DEMO-001"
SOURCE_NAME = "fictional-calibration-run.csv"
REFERENCE_NAME = "fictional-calibration-reference.csv"


def example_files():
    start = datetime(2026, 1, 5, 8, tzinfo=timezone.utc)
    source, reference = StringIO(), StringIO()
    source_writer = csv.writer(source, lineterminator="\n")
    reference_writer = csv.writer(reference, lineterminator="\n")
    source_writer.writerow(CSV_HEADER)
    reference_writer.writerow(REFERENCE_COLUMNS)
    rows = []
    for index, model_x in enumerate((-2.0, 1.5, 0.0, 2.0, -1.0)):
        utc = start + timedelta(seconds=index)
        device_clock = utc + timedelta(milliseconds=40)
        # Declared direction is -X in model coordinates. Simulate gain 2, bias +0.1 m/s².
        raw = (-model_x - 0.1) / 2
        source_writer.writerow([device_clock.isoformat(), VEHICLE_CODE, 0, round(raw, 6), 0])
        rows.append((index + 2, utc.isoformat(), model_x, index / 10, 0))
    source_bytes = source.getvalue().encode("utf-8")
    digest = sha256(source_bytes).hexdigest()
    reference_writer.writerows((digest, *row) for row in rows)
    return {SOURCE_NAME: source_bytes, REFERENCE_NAME: reference.getvalue().encode("utf-8")}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New directory for the two fictional files")
    folder = parser.parse_args().output
    folder.mkdir(parents=True)
    for filename, content in example_files().items():
        path = folder / filename
        path.write_bytes(content)
        print(path.resolve())


if __name__ == "__main__":
    main()
