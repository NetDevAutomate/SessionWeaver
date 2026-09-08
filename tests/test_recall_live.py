"""Disposable real-corpus smoke proof for concept-first AND->OR recall.

Opt-in only: marked ``live`` and deselected by default via pyproject.toml's
``addopts = "... -m 'not live'"``. Run explicitly with ``-m live``.

Mutates only a fresh SQLite Online Backup under ``/tmp``; the real
``~/.config/studyloop/sessions.db`` and the read-only OKF corpus under
``~/.local/share/sessionweaver/poc-storage-decision/okf-store`` are opened
read-only, and their sentinels/hash are proved unchanged in ``finally``.
Pattern: ``tests/test_projection_live.py`` (Online Backup, temp config,
sentinels, cleanup in ``finally``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
import yaml
from agent_session_tools.context.public import open_context
from agent_session_tools.context.scope import visibility_sql

from session_weaver.concepts import ConceptService
from session_weaver.recall import recall

_EVIDENCE_SCHEMA = "session-weaver.recall-smoke-baseline"
_EVIDENCE_VERSION = 1

_DEFAULT_SESSIONS_DB = Path.home() / ".config" / "studyloop" / "sessions.db"
_DEFAULT_OKF_STORE = (
    Path.home() / ".local" / "share" / "sessionweaver" / "poc-storage-decision" / "okf-store"
)

_BASELINE_KEYS = frozenset(
    {
        "evidence_schema",
        "evidence_version",
        "captured_at_utc",
        "import_seconds",
        "queries",
        "cleanup_ok",
        "source_sentinels_unchanged",
        "okf_source_sentinel_unchanged",
    }
)
_QUERY_KEYS = frozenset({"concepts", "sessions", "fallback_used", "seconds"})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    """Deterministic hash over every regular file's relative name and bytes."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _sentinels(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
        "session_count": int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
        "message_count": int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
    }


def _create_online_backup(source: Path, backup: Path) -> dict[str, int]:
    with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as source_conn:
        source_conn.execute("PRAGMA query_only = ON")
        source_conn.execute("BEGIN")
        try:
            sentinels = _sentinels(source_conn)
            with closing(sqlite3.connect(backup)) as backup_conn:
                source_conn.backup(backup_conn)
            return sentinels
        finally:
            source_conn.rollback()


def _read_sentinels(source: Path) -> dict[str, int]:
    with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        try:
            return _sentinels(conn)
        finally:
            conn.rollback()


def _newest_visible_project_basename(db: Path) -> str | None:
    """Return the newest scope-visible session's non-empty project basename."""
    with open_context(db, project=None) as context:
        clause, params = visibility_sql(
            context.conn, "s.id", policy=context.policy, scope=context.scope
        )
        rows = context.conn.execute(
            "SELECT project_path FROM sessions s"
            " WHERE project_path IS NOT NULL AND trim(project_path)!='' AND "
            + clause
            + " ORDER BY updated_at DESC LIMIT 200",
            params,
        ).fetchall()
    for (project_path,) in rows:
        basename = Path(str(project_path)).name
        if basename:
            return basename
    return None


def _validate_baseline_schema(evidence: Mapping[str, Any]) -> None:
    """Structural schema check for recall-smoke-baseline.json.

    No concept/session IDs, questions, prose, or paths may appear anywhere in
    the retained document -- aggregates (counts, timings, booleans) only.
    """
    assert set(evidence) == _BASELINE_KEYS
    assert evidence["evidence_schema"] == _EVIDENCE_SCHEMA
    assert evidence["evidence_version"] == _EVIDENCE_VERSION
    assert isinstance(evidence["captured_at_utc"], str) and evidence["captured_at_utc"]
    assert isinstance(evidence["import_seconds"], (int, float)) and evidence["import_seconds"] >= 0

    queries = evidence["queries"]
    assert set(queries) == {"newest_project_basename", "nonsense_token"}
    for query in queries.values():
        assert set(query) == _QUERY_KEYS
        assert isinstance(query["concepts"], int) and query["concepts"] >= 0
        assert isinstance(query["sessions"], int) and query["sessions"] >= 0
        assert isinstance(query["fallback_used"], bool)
        assert isinstance(query["seconds"], (int, float)) and query["seconds"] >= 0

    for key in ("cleanup_ok", "source_sentinels_unchanged", "okf_source_sentinel_unchanged"):
        assert isinstance(evidence[key], bool)

    serialized = json.dumps(evidence, sort_keys=True)
    for forbidden in ("legacy:", "assertion", "/Users", "/home", str(Path.home())):
        assert forbidden not in serialized


def _write_baseline_evidence(evidence: Mapping[str, Any], output: Path) -> None:
    output.write_text(
        json.dumps(dict(evidence), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _require_newest_visible_hit(*, concepts: int, sessions: int) -> None:
    assert concepts + sessions >= 1, (
        "recall of the newest visible session project basename returned no results"
    )


def _require_okf_source_unchanged(unchanged: bool) -> None:
    assert unchanged is True, "OKF source tree changed during live recall smoke"


def test_newest_visible_retrieval_failure_is_a_hard_failure() -> None:
    with pytest.raises(AssertionError, match="newest visible session project basename"):
        _require_newest_visible_hit(concepts=0, sessions=0)


def test_okf_source_mutation_is_a_hard_failure() -> None:
    with pytest.raises(AssertionError, match="OKF source tree changed"):
        _require_okf_source_unchanged(False)


@pytest.mark.live
def test_real_corpus_recall_smoke() -> None:
    """Disposable real-corpus smoke: import OKF, recall the newest project, recall nonsense.

    Mutates only a disposable SQLite Online Backup. The real database and the
    OKF source tree are never written to.
    """
    if not _DEFAULT_SESSIONS_DB.is_file():
        pytest.skip(f"no live database at {_DEFAULT_SESSIONS_DB}")
    if not _DEFAULT_OKF_STORE.is_dir():
        pytest.skip(f"no live OKF store at {_DEFAULT_OKF_STORE}")

    okf_hash_before = _sha256_tree(_DEFAULT_OKF_STORE)

    backup_dir = Path(tempfile.mkdtemp(prefix="session-weaver-recall-live-"))
    backup_path = backup_dir / "sessions.db"
    home_dir = backup_dir / "home"
    home_dir.mkdir()
    config_path = backup_dir / "config.yaml"

    old_env = {
        key: os.environ.get(key)
        for key in ("HOME", "STUDYLOOP_CONFIG", "STUDYLOOP_DB", "DATABASE_PATH")
    }
    before_sentinels: dict[str, int] | None = None
    try:
        before_sentinels = _create_online_backup(_DEFAULT_SESSIONS_DB, backup_path)

        config_path.write_text(
            yaml.safe_dump(
                {
                    "memory": {"default_scope": "unclassified", "projects": {}},
                    "database": {
                        "path": str(backup_path),
                        "archive_path": str(backup_dir / "sessions-archive.db"),
                        "backup_dir": str(backup_dir / "backups"),
                    },
                    "logging": {"path": str(backup_dir / "sessions.log")},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        os.environ["HOME"] = str(home_dir)
        os.environ["STUDYLOOP_CONFIG"] = str(config_path)
        os.environ.pop("STUDYLOOP_DB", None)
        os.environ.pop("DATABASE_PATH", None)

        service = ConceptService(backup_path)

        started = perf_counter()
        import_report = service.import_okf(_DEFAULT_OKF_STORE, actor="a4-recall-live-smoke")
        import_seconds = perf_counter() - started
        assert import_report.write_failures == 0

        basename = _newest_visible_project_basename(backup_path)
        assert basename is not None, (
            "no scope-visible session with a project path in the live backup"
        )

        started = perf_counter()
        newest_report = recall(backup_path, basename)
        newest_seconds = perf_counter() - started
        _require_newest_visible_hit(
            concepts=len(newest_report.concepts), sessions=len(newest_report.sessions)
        )

        started = perf_counter()
        nonsense_report = recall(backup_path, "zzz-nonsense-token-never-matches-anything-123")
        nonsense_seconds = perf_counter() - started
        assert nonsense_report.concepts == ()
        assert nonsense_report.sessions == ()

        captured_at_utc = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        queries = {
            "newest_project_basename": {
                "concepts": len(newest_report.concepts),
                "sessions": len(newest_report.sessions),
                "fallback_used": newest_report.plan.fallback_used,
                "seconds": round(newest_seconds, 6),
            },
            "nonsense_token": {
                "concepts": len(nonsense_report.concepts),
                "sessions": len(nonsense_report.sessions),
                "fallback_used": nonsense_report.plan.fallback_used,
                "seconds": round(nonsense_seconds, 6),
            },
        }
    finally:
        cleanup_errors: list[str] = []
        try:
            shutil.rmtree(backup_dir, ignore_errors=False)
        except OSError as exc:
            cleanup_errors.append(type(exc).__name__)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert before_sentinels is not None
    after_sentinels = _read_sentinels(_DEFAULT_SESSIONS_DB)
    okf_hash_after = _sha256_tree(_DEFAULT_OKF_STORE)

    evidence: dict[str, Any] = {
        "evidence_schema": _EVIDENCE_SCHEMA,
        "evidence_version": _EVIDENCE_VERSION,
        "captured_at_utc": captured_at_utc,
        "import_seconds": round(import_seconds, 6),
        "queries": queries,
        "cleanup_ok": not cleanup_errors,
        "source_sentinels_unchanged": after_sentinels == before_sentinels,
        "okf_source_sentinel_unchanged": okf_hash_after == okf_hash_before,
    }

    _validate_baseline_schema(evidence)
    assert evidence["cleanup_ok"] is True
    assert evidence["source_sentinels_unchanged"] is True
    _require_okf_source_unchanged(evidence["okf_source_sentinel_unchanged"])

    baseline_path = (
        Path(__file__).resolve().parent.parent / "docs" / "data" / "recall-smoke-baseline.json"
    )
    _write_baseline_evidence(evidence, baseline_path)


def test_recall_smoke_baseline_matches_documented_schema() -> None:
    """Non-live: the committed baseline file must match the sanitized schema."""
    baseline_path = (
        Path(__file__).resolve().parent.parent / "docs" / "data" / "recall-smoke-baseline.json"
    )
    if not baseline_path.is_file():
        pytest.skip("docs/data/recall-smoke-baseline.json has not been captured yet")
    evidence = json.loads(baseline_path.read_text(encoding="utf-8"))
    _validate_baseline_schema(evidence)
