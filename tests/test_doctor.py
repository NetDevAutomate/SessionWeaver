"""Doctor: the environment check against real (temporary) stores."""

import sqlite3

from session_weaver.cli import main


def _make_store(path):
    with sqlite3.connect(path) as conn:
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
