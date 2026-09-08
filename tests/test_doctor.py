"""Doctor: session-store and ontology health through one read-only connection."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol

import pytest
import yaml
from agent_session_tools.context.scope import ScopePolicy, apply_policy

import session_weaver.ontology as ontology
from session_weaver.cli import main
from session_weaver.concepts import ConceptService
from session_weaver.ontology import EXTRACTION_VERSION, rebuild_ontology
from session_weaver.recall import RecallReport, plan


class ProductionStore(Protocol):
    """The production-schema fixture surface used by doctor tests."""

    conn: sqlite3.Connection
    db_path: Path
    config_path: Path


def _insert_concept_sentinel(store: ProductionStore) -> None:
    _insert_scoped_concept(
        store,
        session_id="fixture-session-1",
        term="Doctorrecallsentinel",
        title="Doctorrecallsentinel memory probe",
    )


def _insert_scoped_concept(
    store: ProductionStore,
    *,
    session_id: str,
    term: str,
    title: str,
) -> str:
    quote = store.conn.execute(
        "SELECT body FROM context_evidence WHERE session_id=? ORDER BY id LIMIT 1",
        (session_id,),
    ).fetchone()[0]
    result = ConceptService(store.db_path).winddown(
        session_id,
        {
            "concepts": [
                {
                    "type": "Finding",
                    "title": title,
                    "description": f"{term} proves concept recall is operational.",
                    "tags": ["doctor", "positive-control"],
                    "confidence": 0.9,
                    "quotes": [{"quote": quote}],
                }
            ]
        },
        actor="doctor-test",
    )
    assert result.writes > 0
    return result.concept_ids[0]


def _install_mcp_configs(home: Path) -> None:
    (home / ".claude.json").write_text(
        '{"mcpServers":{"session-db":{"command":"session-db-mcp"}}}\n',
        encoding="utf-8",
    )
    kiro = home / ".kiro" / "settings"
    kiro.mkdir(parents=True)
    (kiro / "mcp.json").write_text(
        '{"mcpServers":{"session-db":{"command":"session-db-mcp"}}}\n',
        encoding="utf-8",
    )
    codex = home / ".codex"
    codex.mkdir()
    (codex / "config.toml").write_text(
        '[mcp_servers.session-db]\ncommand = "session-db-mcp"\n',
        encoding="utf-8",
    )


def _install_grok_skill(home: Path) -> None:
    target = home / ".grok" / "skills" / "session-weaver"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: session-weaver\n---\n", encoding="utf-8")


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
    _insert_concept_sentinel(store)


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
    _install_mcp_configs(Path.home())
    _install_grok_skill(Path.home())

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "3 sessions / 4 messages" in out
    assert f"ok    ontology {EXTRACTION_VERSION}" in out
    assert "coverage=100.00%" in out
    assert "fresh=ok" in out
    assert "hash=ok" in out
    assert "ok    concept sidecar schema=2 rows=1 fts-consistent digest=" in out
    assert "ok    recall positive control term=doctorrecallsentinel results=" in out
    assert "ok    mcp claude session-db registered" in out
    assert "ok    mcp kiro session-db registered" in out
    assert "ok    mcp codex session-db registered" in out
    assert "ok    grok skill installed" in out


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


def test_doctor_closes_every_read_connection(
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
    assert len(opened) >= 2
    for conn in opened:
        _assert_closed(conn)


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


def test_doctor_reports_blob_timestamp_and_continues_independent_checks(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)
    production_store.conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE id = 'doctor-sentinel'",
        (sqlite3.Binary(b"not-text"),),
    )
    production_store.conn.commit()

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "malformed non-null session updated_at values: doctor-sentinel" in out
    assert "ok    session-export ->" in out
    assert "ok    session-query ->" in out
    assert "ok    session-sync ->" in out
    assert "ok    session-repair ->" in out


def test_doctor_contains_unexpected_ontology_failure_and_continues_checks(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)

    def fail_status(_conn: sqlite3.Connection) -> None:
        raise TypeError("unexpected corrupt health value")

    monkeypatch.setattr("session_weaver.cli.ontology_status", fail_status)

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "FAIL  ontology inspection failed" in out
    assert "ok    session-export ->" in out
    assert "ok    session-query ->" in out
    assert "ok    session-sync ->" in out
    assert "ok    session-repair ->" in out


def test_doctor_treats_mcp_registration_and_missing_grok_skill_as_report_only(
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
    assert "INFO  mcp claude session-db not registered" in out
    assert "INFO  mcp kiro session-db not registered" in out
    assert "INFO  mcp codex session-db not registered" in out
    assert "INFO  grok skill absent" in out


def test_doctor_uses_later_visible_recall_candidate_after_unauthorized_first_candidate(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _insert_doctor_sentinel(production_store.conn)
    monkeypatch.setattr(ontology, "_utc_now", lambda: "9998-01-01T00:00:00Z")
    rebuild_ontology(production_store.conn)
    candidates = [
        (
            _insert_scoped_concept(
                production_store,
                session_id="fixture-session-1",
                term="Alphahiddendoctorcontrol",
                title="Alphahiddendoctorcontrol hidden",
            ),
            "fixture-session-1",
            "alphahiddendoctorcontrol",
        ),
        (
            _insert_scoped_concept(
                production_store,
                session_id="fixture-session-2",
                term="Zetavisibledoctorcontrol",
                title="Zetavisibledoctorcontrol visible",
            ),
            "fixture-session-2",
            "zetavisibledoctorcontrol",
        ),
    ]
    first, later = sorted(candidates, key=lambda candidate: candidate[0])
    config = yaml.safe_load(production_store.config_path.read_text(encoding="utf-8"))
    config["memory"]["projects"] = {
        "hidden-project": {"scope": "personal", "roots": []},
        "visible-project": {"scope": "work", "roots": []},
    }
    production_store.config_path.write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    apply_policy(
        production_store.conn,
        ScopePolicy.from_config(config),
        actor="doctor-scope-test",
        dry_run=False,
    )
    production_store.conn.executemany(
        "INSERT INTO context_session_projects VALUES (?,?,?)",
        (
            (first[1], "hidden-project", "explicit"),
            (later[1], "visible-project", "explicit"),
        ),
    )
    production_store.conn.commit()
    monkeypatch.setenv("SESSION_CONTEXT_SCOPE", "work")
    _install_tool_stubs(tmp_path, monkeypatch)

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 0
    assert f"ok    recall positive control term={later[2]} results=" in out
    assert f"term={first[2]}" not in out


def test_doctor_treats_inconsistent_concept_sidecar_as_fatal(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)
    production_store.conn.execute("DELETE FROM context_concept_fts")
    production_store.conn.commit()

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "FAIL  concept sidecar FTS inconsistent" in out


def test_doctor_treats_empty_recall_positive_control_as_fatal(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_healthy_store(production_store, monkeypatch)
    _install_tool_stubs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "session_weaver.cli.recall",
        lambda *_args, **_kwargs: RecallReport(
            concepts=(), sessions=(), plan=plan("doctorrecallsentinel"), k=1, project=None
        ),
    )

    rc = main(["doctor", "--db", str(production_store.db_path)])
    out = capsys.readouterr().out

    assert rc == 1
    assert "FAIL  recall positive control returned no results" in out
