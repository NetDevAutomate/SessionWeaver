"""CLI integration: the real entry point end-to-end against isolated stores."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

import pytest

from session_weaver.cli import main
from session_weaver.harnesses import hub_dir
from session_weaver.installer import SKILL_NAME
from session_weaver.ontology import EXTRACTION_VERSION, rebuild_ontology


class ProductionStore(Protocol):
    """The production-schema fixture surface used by CLI tests."""

    conn: sqlite3.Connection
    db_path: Path


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _assert_closed(conn: sqlite3.Connection) -> None:
    try:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
    finally:
        conn.close()


def test_install_all_then_status_and_uninstall(fake_home: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["install", "--harness", "all"]) == 0
    assert (hub_dir(fake_home) / SKILL_NAME / "SKILL.md").is_file()
    out = capsys.readouterr().out
    assert "hub-install" in out and "link" in out and "skip" in out

    assert main(["status"]) == 0

    assert main(["uninstall", "--harness", "all", "--remove-hub"]) == 0
    assert not (hub_dir(fake_home) / SKILL_NAME).exists()


def test_install_dry_run_touches_nothing(fake_home: Path):
    assert main(["install", "--harness", "kiro", "--dry-run"]) == 0
    assert not (fake_home / ".agents").exists()
    assert not (fake_home / ".kiro").exists()


def test_unknown_harness_is_a_usage_error(
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
):
    assert main(["install", "--harness", "nonesuch"]) == 2
    assert "unknown harness" in capsys.readouterr().err


def test_conflict_exits_nonzero(fake_home: Path, capsys: pytest.CaptureFixture[str]):
    occupied = fake_home / ".kiro/skills" / SKILL_NAME
    occupied.mkdir(parents=True)
    (occupied / "keep.txt").write_text("x")

    assert main(["install", "--harness", "kiro"]) == 1
    assert "conflict" in capsys.readouterr().out


def test_copy_conflict_exits_nonzero_without_changing_existing_file(
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
):
    occupied = fake_home / ".kiro/skills" / SKILL_NAME
    occupied.mkdir(parents=True)
    sentinel = occupied / "SKILL.md"
    sentinel.write_bytes(b"user-owned skill\x00\xff")

    assert main(["install", "--harness", "kiro", "--copy"]) == 1
    assert "conflict" in capsys.readouterr().out
    assert sentinel.read_bytes() == b"user-owned skill\x00\xff"


def test_copy_force_replaces_named_target_and_reports_outcome(
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
):
    occupied = fake_home / ".kiro/skills" / SKILL_NAME
    occupied.mkdir(parents=True)
    sentinel = occupied / "SKILL.md"
    sentinel.write_bytes(b"replace me")
    extra = occupied / "user-only.txt"
    extra.write_bytes(b"remove with forced target")
    sibling = occupied.parent / "keep-me.txt"
    sibling.write_bytes(b"outside named target")

    assert main(["install", "--harness", "kiro", "--copy", "--force"]) == 0
    assert "replaced existing target for kiro" in capsys.readouterr().out
    assert sentinel.read_bytes() != b"replace me"
    assert not extra.exists()
    assert sibling.read_bytes() == b"outside named target"


def test_force_without_copy_is_a_usage_error(
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
):
    assert main(["install", "--harness", "kiro", "--force"]) == 2
    assert "--force requires --copy" in capsys.readouterr().err


def test_uninstall_plain_file_is_a_visible_conflict(
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
):
    target = fake_home / ".kiro/skills" / SKILL_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user-owned plain file\x00\xff")

    assert main(["uninstall", "--harness", "kiro"]) == 1
    assert "conflict" in capsys.readouterr().out
    assert target.read_bytes() == b"user-owned plain file\x00\xff"


def test_console_script_shape_is_importable_and_runs():
    """The installed-entry-point path: python -m equivalent smoke."""
    code = "from session_weaver.cli import main; raise SystemExit(main(['--version']))"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "session-weaver" in proc.stdout


def test_doctor_reports_missing_store(fake_home: Path, capsys: pytest.CaptureFixture[str]):
    rc = main(["doctor", "--db", str(fake_home / "nope.db")])
    out = capsys.readouterr().out
    assert "session store missing" in out
    assert rc == 1


def test_ontology_rebuild_and_status_emit_deterministic_sanitized_json(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = str(production_store.db_path)

    assert main(["ontology", "rebuild", "--db", db]) == 0
    rebuild_output = capsys.readouterr().out
    rebuilt = json.loads(rebuild_output)

    assert rebuilt["command"] == "ontology rebuild"
    assert rebuilt["ok"] is True
    assert rebuilt["mode"] == "full"
    assert rebuilt["fallback_reason"] is None
    assert rebuilt["extraction_version"] == EXTRACTION_VERSION
    assert rebuilt["counts"]["source_sessions"] == 2
    assert rebuilt["counts"]["source_messages"] == 4
    assert len(rebuilt["logical_hash"]) == 64
    assert rebuilt["elapsed_seconds"] >= 0

    assert main(["ontology", "status", "--db", db]) == 0
    status_output = capsys.readouterr().out
    status = json.loads(status_output)

    assert status["command"] == "ontology status"
    assert status["healthy"] is True
    assert status["coverage_ratio"] == 1.0
    assert status["missing_sessions"] == 0
    assert status["foreign_key_violations"] == 0
    assert status["domain_range_violations"] == 0
    assert status["hash_matches"] is True
    for forbidden in (
        db,
        "fixture-project",
        "How does fixture",
        "Through commit_batch",
    ):
        assert forbidden not in rebuild_output
        assert forbidden not in status_output


def test_ontology_incremental_rebuild_reports_actual_mode_and_fallback(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rebuild_ontology(production_store.conn)

    assert (
        main(["ontology", "rebuild", "--incremental", "--db", str(production_store.db_path)]) == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "incremental"
    assert payload["fallback_reason"] is None
    assert payload["candidate_sessions"] == 0


def test_ontology_status_defaults_read_only_and_never_creates_missing_db(
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    default_db = fake_home / ".config/studyloop/sessions.db"

    assert main(["ontology", "status"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.err)

    assert payload == {
        "command": "ontology status",
        "error": "database unavailable",
        "healthy": False,
    }
    assert not default_db.exists()
    assert str(default_db) not in captured.err


def test_ontology_nested_usage_errors_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["ontology"])

    assert raised.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_ontology_commands_close_owned_connections_on_success_and_failure(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rebuild_ontology(production_store.conn)
    invalid_db = tmp_path / "invalid.db"
    invalid_db.write_bytes(b"not sqlite")
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []
    connect_calls: list[tuple[Any, dict[str, Any]]] = []

    def tracked_connect(
        database: Any,
        *args: Any,
        **kwargs: Any,
    ) -> sqlite3.Connection:
        connect_calls.append((database, kwargs))
        conn = real_connect(database, *args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    assert main(["ontology", "status", "--db", str(production_store.db_path)]) == 0
    capsys.readouterr()
    assert main(["ontology", "rebuild", "--db", str(production_store.db_path)]) == 0
    capsys.readouterr()
    assert main(["ontology", "status", "--db", str(invalid_db)]) == 1
    capsys.readouterr()
    assert main(["ontology", "rebuild", "--db", str(invalid_db)]) == 1
    capsys.readouterr()

    assert len(opened) == 4
    assert "mode=ro" in str(connect_calls[0][0])
    assert connect_calls[0][1].get("uri") is True
    assert isinstance(connect_calls[1][0], Path)
    assert "mode=ro" in str(connect_calls[2][0])
    assert connect_calls[2][1].get("uri") is True
    assert isinstance(connect_calls[3][0], Path)
    for conn in opened:
        _assert_closed(conn)
