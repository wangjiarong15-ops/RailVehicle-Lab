"""Generate small, deterministic and explicitly fictional demonstration runs."""

import csv
from datetime import datetime, timedelta, timezone
import io
import math
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


def _render_demo_csv(batch_index: int) -> str:
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
    contents = (_render_demo_csv(0), _render_demo_csv(1))
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
