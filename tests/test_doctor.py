"""Doctor: the environment check against real (temporary) stores."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from session_weaver.cli import main


def _make_store(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('s1')")
        conn.execute("INSERT INTO messages VALUES ('m1','s1')")


def test_doctor_reads_a_healthy_store(tmp_path, capsys, monkeypatch):
    db = tmp_path / "sessions.db"
    _make_store(db)
    # PATH cleared: the tools' absence must be reported, not crash.
    monkeypatch.setenv("PATH", str(tmp_path))

    rc = main(["doctor", "--db", str(db)])
    out = capsys.readouterr().out

    assert "1 sessions / 1 messages" in out
    assert "session-export not on PATH" in out
    assert rc == 1  # missing tools still fail the check


def test_doctor_closes_its_read_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "sessions.db"
    _make_store(db)
    monkeypatch.setenv("PATH", str(tmp_path))
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracked_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    rc = main(["doctor", "--db", str(db)])

    assert rc == 1
    assert len(opened) == 1
    conn = opened[0]
    try:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
    finally:
        conn.close()


def test_doctor_reports_unreadable_store(tmp_path, capsys, monkeypatch):
    db = tmp_path / "sessions.db"
    db.write_text("this is not a sqlite database at all")
    monkeypatch.setenv("PATH", str(tmp_path))

    rc = main(["doctor", "--db", str(db)])
    out = capsys.readouterr().out

    assert "unreadable" in out
    assert rc == 1


def test_doctor_finds_tools_on_path(tmp_path, capsys, monkeypatch):
    db = tmp_path / "sessions.db"
    _make_store(db)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("session-export", "session-query", "session-sync", "session-repair"):
        stub = bin_dir / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    rc = main(["doctor", "--db", str(db)])
    out = capsys.readouterr().out

    assert rc == 0
    assert out.count("ok    session-") == 4
