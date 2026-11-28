"""Versioned local references with page or line anchors and attachment integrity."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import PurePosixPath
from uuid import uuid4

from rail_vehicle.copilot.knowledge import Passage, document_passages, search
from rail_vehicle.db import DB_PATH, get_connection

MAX_LIBRARY_BYTES = 20_000_000


def _parse(content, title):
    suffix = PurePosixPath(title.lower()).suffix
    if suffix == ".pdf":
        return [(item.source.rsplit(" · ", 1)[-1], item.text)
                for item in document_passages(content, title)]
    if suffix not in (".md", ".txt") or len(content) > 128_000:
        raise ValueError("请选择 128 KB 以内的 Markdown/TXT，或 2 MB 以内的文字型 PDF。")
    try:
        lines = content.decode("utf-8-sig").splitlines()
    except UnicodeError:
        raise ValueError("请将工程资料保存为 UTF-8 文本。") from None
    passages = []
    for first in range(0, len(lines), 12):
        block = "\n".join(lines[first:first + 12]).strip()
        for start in range(0, len(block), 900):
            passages.append((f"行 {first + 1}–{min(first + 12, len(lines))}", block[start:start + 900]))
    if not 1 <= len(passages) <= 512:
        raise ValueError("资料须含有文字，每个版本最多索引 512 段。")
    return passages


def import_document(project, filename, source, content, db_path=DB_PATH):
    title = PurePosixPath(filename.replace("\\", "/")).name
    source = source.strip()
    if not isinstance(content, bytes) or not 1 <= len(title) <= 120 or not 1 <= len(source) <= 250:
        raise ValueError("请填写资料来源及授权说明，文件名不得超过 120 个字符。")
    parsed = _parse(content, title)
    checksum = sha256(content).hexdigest()
    with get_connection(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        last = db.execute("SELECT document_id,revision,sha256,source FROM engineering_library "
                          "WHERE project_id=? AND title=? ORDER BY revision DESC LIMIT 1", (project, title)).fetchone()
        if last and last["sha256"] == checksum and last["source"] == source:
            return last["document_id"], last["revision"]
        size = db.execute("SELECT coalesce(sum(length(original)),0) FROM engineering_library WHERE project_id=?",
                          (project,)).fetchone()[0]
        if size + len(content) > MAX_LIBRARY_BYTES:
            raise ValueError("此项目的资料库已达到 20 MB 存储上限。")
        identifier, revision = (last["document_id"], last["revision"] + 1) if last else (uuid4().hex, 1)
        passages = [asdict(Passage(f"library.{identifier}.v{revision}.{i}", title, f"{source} · {anchor}", body))
                    for i, (anchor, body) in enumerate(parsed, 1)]
        db.execute("INSERT INTO engineering_library(project_id,document_id,revision,title,source,sha256,passages,original) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (project, identifier, revision, title, source, checksum, json.dumps(passages, ensure_ascii=False), content))
    return identifier, revision


def documents(project, db_path=DB_PATH):
    with get_connection(db_path) as db:
        return [dict(row) for row in db.execute(
            "SELECT document_id,revision,title,source,sha256 FROM engineering_library "
            "WHERE project_id=? ORDER BY title,revision DESC LIMIT 100", (project,))]


def passages(project, query, db_path=DB_PATH):
    with get_connection(db_path) as db:
        rows = db.execute(
            "SELECT l.passages FROM engineering_library l WHERE l.project_id=? AND NOT EXISTS("
            "SELECT 1 FROM engineering_library n WHERE n.project_id=l.project_id "
            "AND n.document_id=l.document_id AND n.revision>l.revision) ORDER BY l.document_id LIMIT 50", (project,))
        local = tuple(Passage(**item) for row in rows for item in json.loads(row["passages"]))
    return search(query, local, builtin=False, limit=6)


def original(project, document, revision, db_path=DB_PATH):
    with get_connection(db_path) as db:
        row = db.execute("SELECT title,original,sha256 FROM engineering_library WHERE project_id=? "
                         "AND document_id=? AND revision=?", (project, document, revision)).fetchone()
    if row is None or sha256(row["original"]).hexdigest() != row["sha256"]:
        raise ValueError("找不到资料版本或原文摘要校验失败。")
    return row["title"], row["original"]
