from pathlib import Path
import sqlite3

import pytest

from rail_vehicle.demo_data import DEMO_FILE_NAMES, DEMO_SAMPLE_COUNT
from scripts.run_browser_checks import isolated_env, seed_database


def test_seed_database_creates_expected_fictional_batches(tmp_path: Path):
    database = tmp_path / "isolated" / "rail_vehicle.db"
    seed_database(database)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT vehicle_code FROM vehicles").fetchall() == [("DEMO-001",)]
        assert conn.execute("SELECT file_name, row_count FROM import_batches ORDER BY id").fetchall() == [
            (name, DEMO_SAMPLE_COUNT) for name in DEMO_FILE_NAMES
        ]
        rows = conn.execute("SELECT timestamp, vertical_accel FROM run_samples WHERE batch_id=2 ORDER BY id").fetchall()
    assert len(rows) == DEMO_SAMPLE_COUNT
    assert rows[70][0].startswith("2026-10-09 09:31:10")
    assert rows[70][1] == pytest.approx(1.76)


def test_seeding_refuses_to_modify_an_existing_database(tmp_path: Path):
    database = tmp_path / "existing.db"
    database.write_bytes(b"fixture sentinel")
    with pytest.raises(FileExistsError):
        seed_database(database)
    assert database.read_bytes() == b"fixture sentinel"


def test_browser_subprocess_env_uses_isolated_storage_and_removes_ai_overrides(tmp_path, monkeypatch):
    overrides = ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "AI_BASE_URL", "STREAMLIT_SECRETS_FILES")
    for key in overrides:
        monkeypatch.setenv(key, "fake-test-value")
    monkeypatch.setenv("RAIL_PORTABLE_HOME", "real-home-placeholder")
    monkeypatch.setenv("RAIL_PUBLIC_DEMO", "true")
    monkeypatch.setenv("AI_ENABLED", "true")
    env = isolated_env(tmp_path)
    assert not any(key in env for key in overrides)
    assert env["RAIL_PORTABLE_HOME"] == str(tmp_path)
    assert env["RAIL_PUBLIC_DEMO"] == env["AI_ENABLED"] == "false"
    assert env["RAIL_SOFTWARE_WEBGL"] == "1"


def test_windows_server_cleanup_targets_only_the_started_process_tree(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from scripts import run_browser_checks as runner

    kill_tree = Mock()
    fake_os = SimpleNamespace(name="nt", environ={"SystemRoot": "test-system-root"})
    monkeypatch.setattr(runner, "os", fake_os)
    monkeypatch.setattr(runner.subprocess, "run", kill_tree)
    server = Mock(pid=1234)
    server.poll.return_value = None
    runner.stop_server(server)
    command = kill_tree.call_args.args[0]
    assert Path(command[0]).name == "taskkill.exe"
    assert command[1:] == ["/PID", "1234", "/T", "/F"]
    server.wait.assert_called_once_with(timeout=8)
