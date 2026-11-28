"""Consistent SQLite backups and restore into a separate, unused path."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import os
import sqlite3
from tempfile import TemporaryDirectory
from time import monotonic


def copy_verified_database(source, target):
    source, target = Path(source).resolve(strict=True), Path(target).absolute()
    if not source.is_file() or target.exists() or target.is_symlink():
        raise ValueError("请选择有效来源和不存在的目标；当前数据库不会被覆盖。")
    deadline = monotonic() + 30

    def check_time(_status, _remaining, _total):
        if monotonic() > deadline:
            raise TimeoutError("数据库备份超过 30 秒，请关闭高负载任务后再试。")

    with TemporaryDirectory(prefix="rail-db-backup-", dir=target.parent) as directory:
        temporary = Path(directory) / "snapshot.sqlite"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as snapshot:
                original.backup(snapshot, pages=256, progress=check_time, sleep=.1, name="main")
                if snapshot.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite 备份完整性未通过。")
                names = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"vehicles", "import_batches"} <= names:
                    raise ValueError("备份缺少 RailVehicle-Lab 的核心工程表。")
        digest = sha256()
        with temporary.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if os.name == "nt":
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
    return digest.hexdigest()
