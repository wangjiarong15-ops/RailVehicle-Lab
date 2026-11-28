"""Persistent task rounds and a bounded, spawn-compatible Windows worker."""

from contextlib import closing
from dataclasses import asdict
import json
import multiprocessing
from pathlib import Path
from platform import python_version
import re
import sqlite3
from threading import Event, Thread
from time import monotonic, time
from uuid import uuid4
import zlib

from jsonschema import Draft202012Validator

from rail_vehicle.copilot.knowledge import Passage
from rail_vehicle.copilot.tools import Work
from rail_vehicle.db import DB_PATH, get_connection
from rail_vehicle.workbench_tools import TOOLS, digest, invoke, read_snapshot, serialize, snapshot

SCHEMA = "rail.workbench.v1"
LIMITS = {"steps": 18, "cost": 34, "rounds": 4, "seconds": 220, "cloud_calls": 3}
LEASE = 35.


def validate(proposal, existing=(), limits=None):
    limits = limits or LIMITS
    if not isinstance(proposal, list) or not 1 <= len(proposal) <= 8:
        raise ValueError("每轮计划需要 1 至 8 个步骤。")
    pending = {}
    previous = {step["id"]: step for step in existing}
    for raw in proposal:
        if (not isinstance(raw, dict) or not {"id", "tool", "args", "depends"} <= raw.keys()
                or not raw.keys() <= {"id", "tool", "args", "depends", "recover"}):
            raise ValueError("计划步骤缺少协议字段。")
        key, name, args, deps = (raw[field] for field in ("id","tool","args","depends"))
        recover = raw.get("recover")
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,27}", key)
                or key in previous or key in pending or not isinstance(name, str) or name not in TOOLS
                or not isinstance(deps, list) or len(deps)>8 or not all(isinstance(dep, str) for dep in deps)
                or len(deps)!=len(set(deps)) or (recover is not None and (not isinstance(recover, str) or recover not in deps))
                or not Draft202012Validator(TOOLS[name].schema).is_valid(args)):
            raise ValueError("步骤 ID、依赖或工具参数无效。")
        serialize(args)
        pending[key] = {**raw, "recover": recover}
    combined = [*existing,*pending.values()]
    if len(combined) > limits["steps"] or sum(TOOLS[s["tool"]].cost for s in combined)>limits["cost"]:
        raise ValueError("累计工具成本或步骤数超出预算。")
    ordered = []
    known = set(previous)
    while pending:
        available = next((key for key, step in pending.items() if set(step["depends"]) <= known), None)
        if available is None:
            raise ValueError("依赖存在循环或引用未知步骤。")
        ordered.append(pending.pop(available))
        known.add(available)
    tool_seen = {(entry["tool"],serialize(entry["args"]),tuple(sorted(entry["depends"]))) for entry in existing}
    for step in ordered:
        signature = (step["tool"],serialize(step["args"]),tuple(sorted(step["depends"])))
        if signature in tool_seen and not step["recover"]:
            raise ValueError("计划含重复调用。复算请改变求解器或声明失败依赖。")
        tool_seen.add(signature)
    return ordered


def create(title, work, *, cost=34, priority=0, db_path=DB_PATH):
    if (not isinstance(title, str) or not 1<=len(title.strip())<=1200 or type(cost) is not int or not 4<=cost<=34
            or type(priority) is not int or priority not in (0,1)):
        raise ValueError("研究目标、队列优先级或成本限制无效。")
    context = {"data": snapshot(work.data), "other": snapshot(work.other), "engineering": work.engineering,
               "notes": [asdict(note) for note in work.notes]}
    context["sha256"] = digest(context)
    read_snapshot(context["data"])
    read_snapshot(context["other"])
    packed = serialize(context)
    if len(packed.encode()) > 5_000_000:
        raise ValueError("本机任务快照超过 5 MB，请缩小批次范围。")
    identifier = uuid4().hex
    with get_connection(db_path) as db:
        db.execute("INSERT INTO workbench_jobs(id,title,context,limits,priority) VALUES (?,?,?,?,?)",
                   (identifier,title.strip(),packed,serialize({**LIMITS,"cost":cost}),priority))
    return identifier


def _steps(rounds):
    flattened = []
    for entry in rounds:
        if digest({"steps":entry["steps"],"origin":entry["origin"]}) != entry["approval"]:
            raise ValueError("计划审批摘要不一致。")
        flattened.extend(entry["steps"])
    return flattened


def job(identifier, db_path=DB_PATH):
    with get_connection(db_path) as db:
        row = db.execute("SELECT * FROM workbench_jobs WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError("找不到研究任务。")
    result = dict(row)
    for key in ("context","rounds","limits"):
        result[key] = json.loads(result[key])
    context = result["context"]
    if set(context) != {"data", "other"} and (
            set(context) != {"data", "other", "engineering", "notes", "sha256"}
            or digest({key: value for key, value in context.items() if key != "sha256"}) != context["sha256"]):
        raise ValueError("工程研究快照摘要校验失败。")
    result["steps"] = _steps(result["rounds"])
    return result


def duplicate_for_replay(identifier, db_path=DB_PATH):
    """Draft an independent run with identical captured inputs and tool parameters."""
    record = job(identifier, db_path)
    if record["status"] not in ("review", "complete") or not record["steps"]:
        raise ValueError("只有完成执行的任务才能复算。")
    clone = uuid4().hex
    with get_connection(db_path) as db:
        db.execute("INSERT INTO workbench_jobs(id,title,context,limits,priority) VALUES (?,?,?,?,?)",
                   (clone, ("历史复算 · " + record["title"])[:1200], serialize(record["context"]),
                    serialize(record["limits"]), record["priority"]))
        db.execute("INSERT INTO engineering_job_replays(job_id,original_id) VALUES (?,?)", (clone, identifier))
    return clone


def archived_round(record, db_path=DB_PATH):
    with get_connection(db_path) as db:
        link = db.execute("SELECT original_id FROM engineering_job_replays WHERE job_id=?", (record["id"],)).fetchone()
    if link is None:
        return None
    original = job(link["original_id"], db_path)
    position = len(record["rounds"])
    if position >= len(original["rounds"]):
        return None
    if record["steps"] != _steps(original["rounds"][:position]):
        raise ValueError("复算已偏离原步骤，请在新任务中执行调整。")
    return validate(original["rounds"][position]["steps"], record["steps"], record["limits"])


def listing(db_path=DB_PATH, *, project=None):
    path = Path(db_path)
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_jobs' AND type='table'").fetchone():
            return []
        select = "SELECT id,title,status,created_at FROM workbench_jobs "
        if project:
            select += "WHERE json_extract(context,'$.engineering.project')=? "
        return [dict(row) for row in db.execute(select + "ORDER BY created_at DESC,rowid DESC LIMIT 30",
                                               (project,) if project else ())]


def record_round(identifier, proposal, origin, db_path=DB_PATH):
    if origin not in ("local","cloud","replay"):
        raise ValueError("研究计划来源无效。")
    if origin == "replay" and proposal != archived_round(job(identifier, db_path), db_path):
        raise ValueError("批准内容与原始归档不符。")
    with get_connection(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,rounds,limits,context FROM workbench_jobs WHERE id=?",(identifier,)).fetchone()
        if not row or row["status"] not in ("draft","review"):
            raise ValueError("当前任务阶段不接受新计划。")
        rounds,limits,context = (json.loads(row[key]) for key in ("rounds","limits","context"))
        if len(rounds)>=limits["rounds"]:
            raise ValueError("已达到计划轮数预算。")
        steps = validate(proposal,_steps(rounds),limits)
        for step in steps:
            scope = TOOLS[step["tool"]].scope
            if (scope!="local" and not context["data"] or scope=="two_batches" and not context["other"]):
                raise ValueError("请先为任务绑定工具所需批次。")
        content = {"steps":steps,"origin":origin}
        rounds.append({**content,"approval":digest(content)})
        db.execute("UPDATE workbench_jobs SET rounds=?, status='queued', control='', error='' WHERE id=?",
                   (serialize(rounds),identifier))


def note_cloud_call(identifier, db_path=DB_PATH):
    with get_connection(db_path) as db:
        updated = db.execute("UPDATE workbench_jobs SET cloud_calls=cloud_calls+1 WHERE id=? "
                             "AND status IN ('draft','review') AND cloud_calls < ?",
                             (identifier,LIMITS["cloud_calls"]))
        if updated.rowcount!=1:
            raise ValueError("已用完云端发起次数，或当前阶段不支持规划。")


def results(identifier, db_path=DB_PATH):
    with get_connection(db_path) as db:
        rows = db.execute("SELECT * FROM workbench_results WHERE job_id=?",(identifier,)).fetchall()
    outputs = {}
    for row in rows:
        value = json.loads(zlib.decompress(row["result"]))
        if digest([identifier,row["step_id"],value]) != row["digest"]:
            raise ValueError("工具输出摘要与归档不一致。")
        outputs[row["step_id"]] = value
    return outputs


def update(identifier, action, db_path=DB_PATH):
    with get_connection(db_path) as db:
        if action in ("pause","cancel"):
            status = "paused" if action=="pause" else "cancelled"
            db.execute("UPDATE workbench_jobs SET control=?, status=CASE WHEN status='running' THEN status ELSE ? END "
                       "WHERE id=? AND status IN ('running','queued')",(action,status,identifier))
        elif action=="resume":
            db.execute("UPDATE workbench_jobs SET status='queued', control='', error='' WHERE id=? "
                       "AND status IN ('paused','error','cancelled') AND rounds!='[]' AND spent < ?",
                       (identifier,LIMITS["seconds"]))
        elif action=="finish":
            db.execute("UPDATE workbench_jobs SET status='complete' WHERE id=? AND status='review'",(identifier,))
        else:
            raise ValueError("未知任务操作。")


def recover(db_path=DB_PATH):
    with get_connection(db_path) as db:
        db.execute("UPDATE workbench_jobs SET status=CASE control WHEN 'cancel' THEN 'cancelled' "
                   "WHEN 'pause' THEN 'paused' ELSE 'queued' END, "
                   "spent=spent+MIN(?,MAX(0,?-heartbeat)), owner='', control='' "
                   "WHERE status='running' AND heartbeat<?",(LEASE,time(),time()-LEASE))


def _claim(owner, db_path):
    recover(db_path)
    with get_connection(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM workbench_jobs WHERE status='running'").fetchone():
            return None
        row = db.execute("SELECT id FROM workbench_jobs WHERE status='queued' ORDER BY priority DESC, rowid LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE workbench_jobs SET status='running', owner=?, heartbeat=? WHERE id=?",
                   (owner,time(),row["id"]))
        return row["id"]


class ControlledStop(Exception):
    pass


def _run(identifier, owner, db_path):
    started = monotonic()
    stopped = Event()
    status, reason = "error","执行中断，已完成步骤保留。"

    def pulse():
        while not stopped.wait(3):
            try:
                with get_connection(db_path) as db:
                    db.execute("UPDATE workbench_jobs SET heartbeat=? WHERE id=? AND owner=?",
                               (time(),identifier,owner))
            except sqlite3.Error:
                continue

    heartbeat = Thread(target=pulse,daemon=True)
    heartbeat.start()
    try:
        record = job(identifier,db_path)
        done = results(identifier,db_path)
        context = record["context"]
        engineering = context.get("engineering") or {}
        work = Work(read_snapshot(context["data"]), read_snapshot(context["other"]),
                    notes=tuple(Passage(**note) for note in context.get("notes", [])),
                    engineering=engineering, inspection_state=engineering.get("inspection"))
        next_check = 0.

        def check(force=False):
            nonlocal next_check
            now = monotonic()
            if now-started+record["spent"] > record["limits"]["seconds"]:
                raise TimeoutError("该任务已用完累计计算时间。")
            if now < next_check and not force:
                return
            next_check = now+.2
            with get_connection(db_path) as db:
                current = db.execute("SELECT control,owner FROM workbench_jobs WHERE id=?",(identifier,)).fetchone()
            if current["owner"]!=owner:
                raise ControlledStop("error")
            if current["control"]:
                raise ControlledStop("paused" if current["control"]=="pause" else "cancelled")

        for step in record["steps"]:
            if step["id"] in done:
                continue
            check(True)
            deps = {key:done[key] for key in step["depends"]}
            recovery = step["recover"]
            required = [item for key,item in deps.items() if key!=recovery]
            if any(item["status"]!="done" for item in required) or (recovery and deps[recovery]["status"]!="failed"):
                value = {"status":"skipped","detail":"依赖或失败恢复条件未满足。"}
            else:
                try:
                    value = {"status":"done",**invoke(step["tool"],step["args"],work,
                                                      {k:v["output"] for k,v in deps.items() if v["status"]=="done"},check)}
                    value["execution"] = {"protocol": SCHEMA, "python": python_version(),
                                          "input_sha256": digest([context, step]),
                                          "context_sha256": context.get("sha256", digest(context))}
                except (ValueError,ImportError,ArithmeticError) as exc:
                    value = {"status":"failed","detail":"工具校验或求解未通过。",
                             "error_type":type(exc).__name__}
                except TimeoutError:
                    raise
                except Exception as exc:
                    if isinstance(exc,ControlledStop):
                        raise
                    value = {"status":"failed","detail":"运行数据或工具发生内部错误。",
                             "error_type":type(exc).__name__}
            check(True)
            with get_connection(db_path) as db:
                saved = db.execute("INSERT INTO workbench_results "
                                   "SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM workbench_jobs WHERE id=? AND owner=?)",
                                   (identifier,step["id"],zlib.compress(serialize(value).encode()),
                                    digest([identifier,step["id"],value]),identifier,owner))
                if saved.rowcount!=1:
                    raise ControlledStop("error")
            done[step["id"]] = value
        status,reason = "review",""
    except ControlledStop as exc:
        status,reason = str(exc),""
    except TimeoutError as exc:
        reason = str(exc)
    except (sqlite3.Error,ValueError,OSError,zlib.error):
        reason = "无法核对任务快照、结果摘要或本机数据库。"
    finally:
        stopped.set()
        heartbeat.join(timeout=4)
        with get_connection(db_path) as db:
            db.execute("UPDATE workbench_jobs SET status=?, error=?, spent=spent+?, owner='', control='' WHERE id=? AND owner=?",
                       (status,reason,monotonic()-started,identifier,owner))


def run_queue(db_path=DB_PATH):
    owner = uuid4().hex
    while identifier := _claim(owner,db_path):
        _run(identifier,owner,db_path)


def dispatch(db_path=DB_PATH):
    process = multiprocessing.get_context("spawn").Process(target=run_queue,args=(str(db_path),),daemon=True)
    process.start()
    return process.pid
