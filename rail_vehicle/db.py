"""SQLite setup and connection helpers."""

from pathlib import Path
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "rail_vehicle.db"


@contextmanager
def get_connection(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Open a connection to the local application database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Create the initial tables if they do not already exist."""
    with get_connection(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_code TEXT NOT NULL UNIQUE,
                vehicle_type TEXT NOT NULL DEFAULT '',
                vehicle_length_m REAL NOT NULL DEFAULT 0,
                mass_kg REAL,
                axle_count INTEGER,
                bogie_count INTEGER NOT NULL DEFAULT 0,
                max_speed_kmh REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Upgrade databases created by the original scaffold without losing rows.
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(vehicles)")
        }
        migrations = {
            "vehicle_length_m": "REAL NOT NULL DEFAULT 0",
            "bogie_count": "INTEGER NOT NULL DEFAULT 0",
            "max_speed_kmh": "REAL NOT NULL DEFAULT 0",
            "notes": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in migrations.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE vehicles ADD COLUMN {name} {definition}"
                )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                vehicle_id INTEGER,
                row_count INTEGER NOT NULL DEFAULT 0,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                vehicle_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                speed_kmh REAL NOT NULL,
                lateral_accel REAL NOT NULL,
                vertical_accel REAL NOT NULL,
                FOREIGN KEY (batch_id) REFERENCES import_batches(id),
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_samples_timestamp "
            "ON run_samples(timestamp)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_samples_vehicle_timestamp "
            "ON run_samples(vehicle_id, timestamp)"
        )
