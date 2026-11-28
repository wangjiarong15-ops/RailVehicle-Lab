"""Local project context and independent, append-only engineering revisions."""

import json
from uuid import uuid4

from rail_vehicle.copilot.context import read_inspection_state
from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.workbench_tools import digest, serialize

SCHEMA = "rail.engineering-context.v1"


def capture(page, data=None, *, case=None, inspection=None, project=None, task=None, constraints=None):
    """Use actual page selections. A snapshot never executes an operation."""
    result = {"schema": SCHEMA, "page": page}
    if data:
        channels = data.sensors.get("channels", {})
        result["observation"] = {
            "identity": data.identity, "source": data.source,
            "vehicle_id": data.vehicle["id"], "batch_id": data.batch["id"],
            "vehicle_configuration_sha256": digest(data.vehicle),
            "source_sha256": data.batch.get("source_sha256"),
            "channel_versions": {name: record["version"] for name, record in channels.items()},
            "row_span": data.row_span, "event": data.preferred_event, "threshold_m_s2": data.threshold,
        }
    if case is not None:
        from rail_vehicle.nonlinear_vehicle import case_config

        result["simulation"] = case_config(case)
    if parsed := read_inspection_state(inspection):
        result["inspection"] = parsed
    if project:
        result["project"] = project
    if task:
        result["task"] = task
    if constraints:
        result["constraints"] = constraints
    return json.loads(serialize(result))


def create_project(name, db_path=DB_PATH):
    name = name.strip() if isinstance(name, str) else ""
    if not 1 <= len(name) <= 80 or any(ord(char) < 32 for char in name):
        raise ValueError("项目名称须为 1 至 80 个可显示字符。")
    identifier = uuid4().hex
    with get_connection(db_path) as db:
        db.execute("INSERT INTO engineering_projects(id,name) VALUES (?,?)", (identifier, name))
    return identifier


def projects(db_path=DB_PATH):
    with get_connection(db_path) as db:
        return [dict(row) for row in db.execute(
            "SELECT id,name,created_at FROM engineering_projects ORDER BY created_at DESC,name LIMIT 100")]


def save_revision(project, context, db_path=DB_PATH):
    payload = serialize(context)
    if context.get("schema") != SCHEMA or context.get("project") != project or len(payload.encode()) > 65_536:
        raise ValueError("工程快照版本、归属或大小无效。")
    with get_connection(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        revision = db.execute("SELECT coalesce(max(revision),0)+1 FROM engineering_project_revisions "
                              "WHERE project_id=?", (project,)).fetchone()[0]
        db.execute("INSERT INTO engineering_project_revisions(project_id,revision,payload,sha256) VALUES (?,?,?,?)",
                   (project, revision, payload, digest(context)))
    return revision


def revisions(project, db_path=DB_PATH):
    with get_connection(db_path) as db:
        rows = db.execute("SELECT revision,created_at,sha256 FROM engineering_project_revisions WHERE project_id=? "
                          "ORDER BY revision DESC LIMIT 50", (project,))
        return [dict(row) for row in rows]


def read_revision(project, revision, db_path=DB_PATH):
    with get_connection(db_path) as db:
        row = db.execute("SELECT payload,sha256 FROM engineering_project_revisions WHERE project_id=? AND revision=?",
                         (project, revision)).fetchone()
    if row is None:
        raise ValueError("找不到此项目版本。")
    context = json.loads(row["payload"])
    if digest(context) != row["sha256"] or context.get("project") != project:
        raise ValueError("工程快照摘要校验失败。")
    return context
