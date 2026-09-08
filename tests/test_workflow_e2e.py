"""Representative fixture and live A7 workflow acceptance."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing, redirect_stderr, redirect_stdout
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from agent_session_tools.context.provenance import Origin
from agent_session_tools.context.store import NativeSource
from agent_session_tools.exporters.base import ExportStats, commit_batch
from agent_session_tools.migrations import migrate

from session_weaver.cli import main
from session_weaver.ontology_live import _create_online_backup, _read_source_sentinels

WORKFLOW = [
    "install",
    "export",
    "ontology_rebuild",
    "winddown",
    "recall",
    "concept_project",
    "doctor",
]
QUOTE = "Fixture exporter routes the sentinel through production capture."


def _run(*args: str) -> str:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = main(list(args))
    assert result == 0, (
        f"session-weaver {' '.join(args)} failed:\nstdout:\n{stdout.getvalue()}"
        f"\nstderr:\n{stderr.getvalue()}"
    )
    return stdout.getvalue()


def _config(root: Path, db_path: Path) -> Path:
    path = root / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "memory": {"default_scope": "unclassified", "projects": {}},
                "database": {
                    "path": str(db_path),
                    "archive_path": str(root / "archive.db"),
                    "backup_dir": str(root / "backups"),
                },
                "logging": {"path": str(root / "sessions.log")},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _native(session_id: str, key: str, body: str, kind: str) -> NativeSource:
    return NativeSource(
        session_id=session_id,
        native_key=key,
        harness="codex",
        native_kind=kind,
        native_locator=f"fixture://codex/{session_id}#{key}",
        parser_version="codex-native-v1",
        machine_id="fixture-machine",
        body=body,
        origin=Origin.CONVERSATION,
        recorded_at="2026-09-07T12:00:00+00:00",
    )


def _export_fixture(db_path: Path, project_path: Path) -> ExportStats:
    session_id = f"workflow-fixture-{uuid4().hex}"
    sessions = [
        {
            "id": session_id,
            "source": "codex",
            "project_path": str(project_path),
            "git_branch": "feat/sessionweaver-phase2",
            "created_at": "2026-09-07T12:00:00+00:00",
            "updated_at": "2026-09-07T12:02:00+00:00",
            "metadata": "{}",
            "status": "added",
            "native_sources": [
                _native(
                    session_id, "session-envelope", "Workflow fixture envelope.", "session:metadata"
                )
            ],
        }
    ]
    messages = [
        {
            "id": f"{session_id}-user",
            "session_id": session_id,
            "role": "user",
            "content": "Which fixture route is authoritative?",
            "model": "fixture-model",
            "timestamp": "2026-09-07T12:01:00+00:00",
            "metadata": "{}",
            "seq": 1,
            "native_sources": [
                _native(
                    session_id,
                    "message-user",
                    "Which fixture route is authoritative?",
                    "message:user",
                )
            ],
        },
        {
            "id": f"{session_id}-assistant",
            "session_id": session_id,
            "role": "assistant",
            "content": QUOTE,
            "model": "fixture-model",
            "timestamp": "2026-09-07T12:02:00+00:00",
            "metadata": "{}",
            "seq": 2,
            "native_sources": [
                _native(session_id, "message-assistant", QUOTE, "message:assistant")
            ],
        },
    ]
    stats = ExportStats()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        commit_batch(conn, sessions, messages, stats)
    assert stats.added == 1
    return stats


def _initialize(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            files("agent_session_tools").joinpath("schema.sql").read_text(encoding="utf-8")
        )
        migrate(conn)


def _counts(db_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in (
                "sessions",
                "messages",
                "context_evidence",
                "ontology_individual",
                "context_concepts",
            )
        }


def _run_workflow(root: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    home = root / "home"
    home.mkdir()
    config = _config(root, db_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STUDYLOOP_CONFIG", str(config))
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("STUDYLOOP_DB", raising=False)
    monkeypatch.delenv("SESSION_CONTEXT_SCOPE", raising=False)

    _run("install", "--harness", "claude")
    stats = _export_fixture(db_path, root / "fixture-project")
    with closing(sqlite3.connect(db_path)) as conn:
        session_id = conn.execute(
            "SELECT id FROM sessions WHERE id LIKE 'workflow-fixture-%' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    _run("ontology", "rebuild", "--db", str(db_path))

    winddown = root / "winddown.json"
    winddown.write_text(
        json.dumps(
            {
                "concepts": [
                    {
                        "type": "Decision",
                        "title": "Use the production fixture route",
                        "description": (
                            "The representative workflow uses production capture and exact "
                            "evidence."
                        ),
                        "tags": ["session-memory", "workflow-e2e"],
                        "confidence": 0.95,
                        "quotes": [{"quote": QUOTE}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _run("winddown", "--session", session_id, "--from", str(winddown), "--db", str(db_path))

    recall = json.loads(
        _run("recall", "production fixture route", "--k", "5", "--db", str(db_path), "--json")
    )
    assert recall["concepts"][0]["title"] == "Use the production fixture route"

    projection_parent = Path(tempfile.mkdtemp(prefix="sessionweaver-a7-", dir="/tmp"))
    projection = projection_parent / "projection"
    try:
        _run("concept", "project", "--out", str(projection), "--db", str(db_path), "--json")
        assert any(projection.glob("*.md"))
    finally:
        shutil.rmtree(projection_parent, ignore_errors=True)

    _run("doctor", "--db", str(db_path))
    return {"exported": stats.added, **_counts(db_path)}


def test_representative_fixture_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "sessions.db"
    _initialize(db_path)

    aggregates = _run_workflow(tmp_path, db_path, monkeypatch)

    assert aggregates["exported"] == 1
    assert aggregates["context_concepts"] == 1
    assert aggregates["ontology_individual"] > 0


@pytest.mark.live
def test_representative_live_online_backup_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_value = os.environ.get("SESSION_WEAVER_WORKFLOW_SOURCE")
    if not source_value:
        pytest.skip("set SESSION_WEAVER_WORKFLOW_SOURCE to run the live Online Backup workflow")
    source = Path(source_value).expanduser().resolve()
    backup = tmp_path / "workflow-live.db"
    before, snapshot_sha256 = _create_online_backup(source, backup)

    aggregates = _run_workflow(tmp_path, backup, monkeypatch)

    after = _read_source_sentinels(source)
    assert after == before
    assert len(snapshot_sha256) == 64
    evidence = {
        "schema": "sessionweaver-workflow-e2e-v1",
        "tested_session_weaver_sha": os.environ.get("SESSION_WEAVER_TESTED_SHA", "dirty-worktree"),
        "source_sentinels_unchanged": True,
        "workflow": WORKFLOW,
        "aggregates": aggregates,
    }
    evidence_path = os.environ.get("SESSION_WEAVER_WORKFLOW_EVIDENCE")
    if evidence_path:
        target = Path(evidence_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    serialized = json.dumps(evidence, sort_keys=True)
    assert str(source) not in serialized
    assert all(isinstance(value, int) for value in aggregates.values())
