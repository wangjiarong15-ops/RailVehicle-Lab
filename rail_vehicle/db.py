"""SQLite setup and connection helpers."""

from pathlib import Path
import os
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
def data_directory(environment=None):
    """Keep regular installs unchanged; portable launcher opts into a user directory."""
    environment = os.environ if environment is None else environment
    configured = environment.get("RAIL_PORTABLE_HOME")
    return Path(configured) / "data" if configured else PROJECT_ROOT / "data"


DATA_DIR = data_directory()
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
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_samples_batch_vehicle ON run_samples(batch_id, vehicle_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                generated_at TEXT NOT NULL,
                provider TEXT NOT NULL,
                analysis_type TEXT NOT NULL DEFAULT 'single_batch',
                data_source TEXT NOT NULL DEFAULT 'history',
                input_fingerprint TEXT NOT NULL,
                selection_fingerprint TEXT NOT NULL,
                selection_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                UNIQUE (generated_at, input_fingerprint, selection_fingerprint)
            )
            """
        )
        history_migrations = {
            "analysis_type": "TEXT NOT NULL DEFAULT 'single_batch'",
            "data_source": "TEXT NOT NULL DEFAULT 'history'",
        }
        history_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_analysis_history)")}
        if history_migrations.keys() - history_columns:
            # Check again after taking the write lock in case another session upgraded first.
            connection.execute("BEGIN IMMEDIATE")
            history_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_analysis_history)")}
            for name, definition in history_migrations.items():
                if name not in history_columns:
                    connection.execute(f"ALTER TABLE ai_analysis_history ADD COLUMN {name} {definition}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_ai_history_type_id ON ai_analysis_history(analysis_type, id)")
