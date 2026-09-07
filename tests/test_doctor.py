"""Doctor: session-store and ontology health through one read-only connection."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol

import pytest

import session_weaver.ontology as ontology
from session_weaver.cli import main
from session_weaver.ontology import EXTRACTION_VERSION, rebuild_ontology


class ProductionStore(Protocol):
    """The production-schema fixture surface used by doctor tests."""

    conn: sqlite3.Connection
    db_path: Path


def _install_tool_stubs(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for tool in ("session-export", "session-query", "session-sync", "session-repair"):
        stub = bin_dir / tool
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


def _insert_doctor_sentinel(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO sessions(
            id, source, project_path, git_branch, created_at, updated_at, metadata
        ) VALUES (
            'doctor-sentinel', 'codex', NULL, NULL,
            '2026-09-07T09:00:00Z', '2026-09-07T10:00:00Z', '{}'
        )
        """
    )
    conn.commit()


def _prepare_healthy_store(
    store: ProductionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _insert_doctor_sentinel(store.conn)
    monkeypatch.setattr(ontology, "_utc_now", lambda: "9998-01-01T00:00:00Z")
    rebuild_ontology(store.conn)


def _assert_closed(conn: sqlite3.Connection) -> None:
    try:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
    finally:
        conn.close()


def test_doctor_reports_healthy_ontology(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "3 sessions / 4 messages" in out
    assert f"ok    ontology {EXTRACTION_VERSION}" in out
    assert "coverage=100.00%" in out
    assert "fresh=ok" in out
    assert "hash=ok" in out


def test_doctor_reports_missing_ontology(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_tool_stubs(tmp_path, monkeypatch)

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "FAIL  ontology unhealthy" in out
    assert "missing ontology table" in out


def test_doctor_reports_stale_ontology_after_known_sentinel_becomes_newer(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)
    production_store.conn.execute(
        "UPDATE sessions SET updated_at = '9999-01-01T00:00:00Z' WHERE id = 'doctor-sentinel'"
    )
    production_store.conn.commit()

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "ontology is stale" in out


def test_doctor_reports_malformed_sentinel_timestamp(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)
    production_store.conn.execute(
        "UPDATE sessions SET updated_at = 'not-a-timestamp' WHERE id = 'doctor-sentinel'"
    )
    production_store.conn.commit()

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "malformed non-null session updated_at values: doctor-sentinel" in out


def test_doctor_reports_logical_hash_tamper(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)
    production_store.conn.execute(
        "UPDATE ontology_individual SET label = 'tampered' WHERE id = 'session:doctor-sentinel'"
    )
    production_store.conn.commit()

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "logical hash mismatch" in out


def test_doctor_closes_its_only_read_connection(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracked_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    assert main(["doctor", "--db", str(production_store.db_path)]) == 0
    assert len(opened) == 1
    _assert_closed(opened[0])


def test_doctor_reports_unreadable_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "sessions.db"
    db.write_text("this is not a sqlite database at all", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))

    rc = main(["doctor", "--db", str(db)])
    out = capsys.readouterr().out

    assert "unreadable" in out
    assert rc == 1


def test_doctor_reports_missing_tools_without_hiding_healthy_ontology(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path))

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert f"ok    ontology {EXTRACTION_VERSION}" in out
    assert "session-export not on PATH" in out
