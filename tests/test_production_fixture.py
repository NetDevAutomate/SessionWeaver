"""Contracts for the isolated production-schema test fixture."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from agent_session_tools.config_loader import load_config
from agent_session_tools.exporters.base import ExportStats
from agent_session_tools.migrations import CURRENT_VERSION


class ProductionStore(Protocol):
    """Resources yielded by the production-schema fixture."""

    conn: sqlite3.Connection
    db_path: Path
    config_path: Path
    stats: ExportStats


def test_production_store_uses_migrations_and_capture_batch(
    production_store: ProductionStore,
) -> None:
    """The fixture reaches the current schema and creates native evidence."""
    conn = production_store.conn

    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
    assert {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("sessions", "messages", "context_evidence")
    } == {
        "sessions": 2,
        "messages": 4,
        "context_evidence": 6,
    }
    assert conn.execute("SELECT COUNT(*) FROM context_native_message_sources").fetchone()[0] == 4
    assert production_store.stats == ExportStats(added=2)


def test_production_store_only_opens_its_temporary_database(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No fixture connection may escape pytest's temporary directory."""
    real_connect = sqlite3.connect
    opened: list[Path] = []

    def guarded_connect(
        database: os.PathLike[str] | str | bytes,
        *args: Any,
        **kwargs: Any,
    ) -> sqlite3.Connection:
        raw_path = os.fsdecode(database)
        if raw_path.startswith("file:"):
            raw_path = raw_path.removeprefix("file:").split("?", maxsplit=1)[0]
        resolved = Path(raw_path).resolve()
        assert resolved.is_relative_to(tmp_path.resolve())
        opened.append(resolved)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    production_store = cast(ProductionStore, request.getfixturevalue("production_store"))

    assert opened == [production_store.db_path.resolve()]
    assert production_store.config_path.resolve().is_relative_to(tmp_path.resolve())
    assert os.environ["STUDYLOOP_CONFIG"] == str(production_store.config_path)
    assert load_config()["memory"]["default_scope"] == "unclassified"
    assert Path(load_config()["database"]["path"]).resolve() == production_store.db_path.resolve()
