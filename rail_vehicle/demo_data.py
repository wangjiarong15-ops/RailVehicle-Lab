"""Generate small, deterministic and explicitly fictional demonstration runs."""

import csv
from datetime import datetime, timedelta, timezone
import io
import math
import random
from pathlib import Path


DEMO_VEHICLE_CODE = "DEMO-001"
DEMO_SAMPLE_COUNT = 120
DEMO_FILE_NAMES = ("demo_run_1.csv", "demo_run_2.csv")
CSV_HEADER = (
    "timestamp",
    "vehicle_id",
    "speed_kmh",
    "lateral_accel",
    "vertical_accel",
)


def ml_demo_samples() -> tuple[list[dict], list[dict]]:
    """Independent fictional reference/target draws; no labels enter the model."""
    generator = random.Random(2026)
    batches = []
    for day, count in ((0, 400), (1, 160)):
        start = datetime(2026, 10, 8 + day, 9, 30, tzinfo=timezone.utc)
        samples = []
        for index in range(count):
            speed = 72 + generator.gauss(0, 2)
            lateral = generator.gauss(0, 0.12)
            vertical = generator.gauss(0, 0.1)
            if day == 1 and 40 <= index <= 45:
                speed = 120 + generator.gauss(0, 1)
            if day == 1 and 100 <= index <= 104:
                lateral = 1.8 + generator.gauss(0, 0.05)
                vertical = -1.6 + generator.gauss(0, 0.05)
            samples.append(dict(
                timestamp=(start + timedelta(seconds=index)).isoformat(),
                speed_kmh=round(speed, 4), lateral_accel=round(lateral, 4),
                vertical_accel=round(vertical, 4),
            ))
        batches.append(samples)
    return batches[0], batches[1]


def vibration_demo_samples() -> list[dict]:
    """Fictional 100 Hz, 30 s signal with known tones and a short amplitude rise."""
    start = datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc)
    samples = []
    for index in range(3000):
        seconds = index / 100
        envelope = 2.5 if 18 <= seconds < 20 else 1.0
        lateral = 0.04 + envelope * (0.25 * math.sin(2 * math.pi * 1.5 * seconds)
                                    + 0.05 * math.sin(2 * math.pi * 8 * seconds))
        vertical = -0.02 + envelope * (0.18 * math.sin(2 * math.pi * 6 * seconds)
                                      + 0.04 * math.sin(2 * math.pi * 12 * seconds))
        samples.append(dict(timestamp=(start + timedelta(milliseconds=index * 10)).isoformat(),
                            speed_kmh=72, lateral_accel=round(lateral, 6),
                            vertical_accel=round(vertical, 6)))
    return samples


def demo_csv_content(batch_index: int) -> str:
    """Return fictional CSV text for offline demonstrations without file writes."""
    start = datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc) + timedelta(
        days=batch_index
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_HEADER)

    for sample_index in range(DEMO_SAMPLE_COUNT):
        speed = 72 + batch_index * 2 + 5 * math.sin(sample_index / 14)
        lateral = 0.18 * math.sin(sample_index / 3)
        vertical = 0.12 * math.cos(sample_index / 5)

        # Deliberate synthetic excursions make the threshold/event UI visible.
        if sample_index in (30, 31, 32):
            lateral = (1.4, 1.8, -1.5)[sample_index - 30]
        if sample_index in (70, 71):
            vertical = (1.6, -1.7)[sample_index - 70]
        if batch_index == 1:
            speed += 1.5
            lateral *= 0.8
            vertical *= 1.1

        timestamp = (start + timedelta(seconds=sample_index)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        writer.writerow(
            (
                timestamp,
                DEMO_VEHICLE_CODE,
                f"{speed:.3f}",
                f"{lateral:.4f}",
                f"{vertical:.4f}",
            )
        )
    return output.getvalue()


def generate_demo_csvs(output_dir: Path | str) -> tuple[Path, Path]:
    """Write two fictional 120-row batches without overwriting existing files."""
    directory = Path(output_dir)
    paths = tuple(directory / name for name in DEMO_FILE_NAMES)
    existing = [path.name for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "演示文件已存在，为避免覆盖，请选择其他目录：" + ", ".join(existing)
        )

    directory.mkdir(parents=True, exist_ok=True)
    contents = (demo_csv_content(0), demo_csv_content(1))
    created: list[Path] = []
    try:
        for path, content in zip(paths, contents):
            with path.open("x", encoding="utf-8", newline="") as stream:
                created.append(path)
                stream.write(content)
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return paths
