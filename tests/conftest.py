"""Shared pytest fixtures for isolated production-schema tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from agent_session_tools.context.provenance import Origin
from agent_session_tools.context.store import NativeSource
from agent_session_tools.exporters.base import ExportStats, commit_batch
from agent_session_tools.migrations import CURRENT_VERSION, migrate


@dataclass(frozen=True)
class ProductionStore:
    """Temporary production-schema database and its isolated configuration."""

    conn: sqlite3.Connection
    db_path: Path
    config_path: Path
    stats: ExportStats


def _native_source(
    *,
    session_id: str,
    harness: str,
    parser_version: str,
    native_key: str,
    native_kind: str,
    body: str,
    origin: Origin,
) -> NativeSource:
    """Build one deterministic native capture record for fixture data."""
    return NativeSource(
        session_id=session_id,
        native_key=native_key,
        harness=harness,
        native_kind=native_kind,
        native_locator=f"fixture://{harness}/{session_id}#{native_key}",
        parser_version=parser_version,
        machine_id="fixture-machine",
        body=body,
        origin=origin,
        recorded_at="2026-09-07T12:00:00+00:00",
    )


def _fixture_rows(project_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return representative sessions and messages with native source records."""
    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    harnesses = (("codex", "codex-native-v1"), ("kiro_cli", "kiro-native-v1"))

    for index, (harness, parser_version) in enumerate(harnesses, start=1):
        session_id = f"fixture-session-{index}"
        sessions.append(
            {
                "id": session_id,
                "source": harness,
                "project_path": str(project_path),
                "git_branch": "feat/sessionweaver-phase2",
                "created_at": f"2026-09-07T12:0{index}:00+00:00",
                "updated_at": f"2026-09-07T12:1{index}:00+00:00",
                "metadata": "{}",
                "status": "added",
                "native_sources": [
                    _native_source(
                        session_id=session_id,
                        harness=harness,
                        parser_version=parser_version,
                        native_key="session-envelope",
                        native_kind="session:metadata",
                        body=f"Fixture envelope for {harness}.",
                        origin=Origin.UNKNOWN,
                    )
                ],
            }
        )
        for seq, (role, content) in enumerate(
            (
                ("user", f"How does fixture session {index} reach context evidence?"),
                ("assistant", "Through commit_batch and production capture_batch."),
            ),
            start=1,
        ):
            message_id = f"fixture-message-{index}-{seq}"
            messages.append(
                {
                    "id": message_id,
                    "session_id": session_id,
                    "role": role,
                    "content": content,
                    "model": "fixture-model",
                    "timestamp": f"2026-09-07T12:2{seq}:00+00:00",
                    "metadata": "{}",
                    "seq": seq,
                    "native_sources": [
                        _native_source(
                            session_id=session_id,
                            harness=harness,
                            parser_version=parser_version,
                            native_key=f"message-{seq}",
                            native_kind=f"message:{role}",
                            body=content,
                            origin=Origin.CONVERSATION,
                        )
                    ],
                }
            )

    return sessions, messages


@pytest.fixture
def production_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ProductionStore]:
    """Yield a migrated, populated store that cannot resolve the live database."""
    home = tmp_path / "home"
    home.mkdir()
    db_path = tmp_path / "sessions.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "memory": {"default_scope": "unclassified", "projects": {}},
                "database": {
                    "path": str(db_path),
                    "archive_path": str(tmp_path / "sessions-archive.db"),
                    "backup_dir": str(tmp_path / "backups"),
                },
                "logging": {"path": str(tmp_path / "sessions.log")},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STUDYLOOP_CONFIG", str(config_path))
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("STUDYLOOP_DB", raising=False)
    monkeypatch.delenv("SESSION_CONTEXT_SCOPE", raising=False)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            files("agent_session_tools").joinpath("schema.sql").read_text(encoding="utf-8")
        )
        migrate(conn)
        if conn.execute("PRAGMA user_version").fetchone()[0] != CURRENT_VERSION:
            raise RuntimeError("production fixture migration did not reach CURRENT_VERSION")

        sessions, messages = _fixture_rows(tmp_path / "fixture-project")
        stats = ExportStats()
        commit_batch(conn, sessions, messages, stats)
        yield ProductionStore(
            conn=conn,
            db_path=db_path,
            config_path=config_path,
            stats=stats,
        )
    finally:
        conn.close()
