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


def _add_columns(connection, table, definitions):
    # Table and column names are constants from the schema below.
    def columns():
        return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}

    if definitions.keys() <= columns():
        return
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    existing = columns()  # Another session might have finished the same upgrade.
    for name, definition in definitions.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Create the initial tables if they do not already exist."""
    with get_connection(db_path) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS twin_research_history (
            evidence_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            report_json TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS coupled_trial_history (
            study_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            report_json TEXT NOT NULL
        )""")
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
        migrations = {
            "vehicle_length_m": "REAL NOT NULL DEFAULT 0",
            "bogie_count": "INTEGER NOT NULL DEFAULT 0",
            "max_speed_kmh": "REAL NOT NULL DEFAULT 0",
            "notes": "TEXT NOT NULL DEFAULT ''",
        }
        _add_columns(connection, "vehicles", migrations)
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
        _add_columns(connection, "ai_analysis_history", history_migrations)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_ai_history_type_id ON ai_analysis_history(analysis_type, id)")
        _add_columns(connection, "import_batches", {"source_sha256": "TEXT", "engineering_json": "TEXT",
                                                    "source_kind": "TEXT NOT NULL DEFAULT 'unknown'"})
        connection.execute("""CREATE TABLE IF NOT EXISTS run_analysis_history (
            id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
            source_sha256 TEXT, source_kind TEXT NOT NULL, threshold REAL NOT NULL,
            first_row INTEGER NOT NULL, last_row INTEGER NOT NULL, batch_count INTEGER NOT NULL,
            start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, rule_version TEXT NOT NULL, analyzed_at TEXT NOT NULL)""")
        _add_columns(connection, "run_samples", {"csv_line": "INTEGER"})
        connection.execute("""CREATE TABLE IF NOT EXISTS sensor_devices (
            serial TEXT PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS sensor_revisions (
            batch_id INTEGER NOT NULL REFERENCES import_batches(id),
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id), channel TEXT NOT NULL,
            version INTEGER NOT NULL, serial TEXT NOT NULL REFERENCES sensor_devices(serial),
            metadata TEXT, changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY(batch_id,vehicle_id,channel,version))""")
