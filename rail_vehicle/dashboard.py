"""Read-only home dashboard queries; no migrations or analysis persistence."""

from pathlib import Path
import sqlite3
from contextlib import closing

from rail_vehicle import db
from rail_vehicle.analysis_history import recent_batch_analyses


def load_dashboard(db_path=None):
    path = Path(db_path if db_path is not None else db.DB_PATH).resolve()
    if not path.exists():
        return dict(vehicle_count=0, batch_count=0, sample_count=0, imports=[], preview=None, recent=[])
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        result = dict(vehicle_count=connection.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0],
                      batch_count=connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0],
                      sample_count=connection.execute("SELECT COUNT(*) FROM run_samples").fetchone()[0])
        result["imports"] = [dict(row) for row in connection.execute(
            "SELECT id, file_name, row_count, imported_at FROM import_batches ORDER BY imported_at DESC, id DESC LIMIT 5")]
        result["recent"] = recent_batch_analyses(connection)
        latest = connection.execute("""SELECT s.batch_id, s.vehicle_id, v.vehicle_code, COUNT(*) AS sample_count
            FROM run_samples s JOIN vehicles v ON v.id=s.vehicle_id JOIN import_batches b ON b.id=s.batch_id
            GROUP BY s.batch_id,s.vehicle_id ORDER BY b.imported_at DESC,b.id DESC,s.vehicle_id LIMIT 1""").fetchone()
        result["preview"] = None
        if latest:
            preview = dict(latest)
            batch_columns = {row["name"] for row in connection.execute("PRAGMA table_info(import_batches)")}
            provenance = [field for field in ("source_kind", "engineering_json") if field in batch_columns]
            if provenance:
                preview.update(dict(connection.execute(f"SELECT {', '.join(provenance)} FROM import_batches WHERE id=?",
                                                       (latest["batch_id"],)).fetchone()))
            sample_columns = {row["name"] for row in connection.execute("PRAGMA table_info(run_samples)")}
            line = "csv_line" if "csv_line" in sample_columns else "NULL AS csv_line"
            preview["samples"] = [dict(row) for row in connection.execute(
                f"SELECT id AS sample_id,timestamp,speed_kmh,lateral_accel,vertical_accel,{line} "
                "FROM run_samples WHERE batch_id=? AND vehicle_id=? ORDER BY timestamp,id LIMIT 20001",
                (latest["batch_id"], latest["vehicle_id"]))] if latest["sample_count"] <= 20000 else []
            result["preview"] = preview
        return result
