"""Disposable real-corpus proof for scope-authorized Markdown projection.

Opt-in only: marked ``live`` and deselected by default via pyproject.toml's
``addopts = "... -m 'not live'"``. Run explicitly with ``-m live``.

Mutates only a fresh SQLite Online Backup under ``/tmp`` and a private tmp
projection directory. The real ``~/.config/studyloop/sessions.db`` and the
read-only OKF corpus under
``~/.local/share/sessionweaver/poc-storage-decision/okf-store`` are opened
read-only, and their sentinels/hash are proved unchanged in ``finally``.
Pattern: ``tests/test_ontology_live.py`` (Online Backup, temp config,
sentinels, cleanup in ``finally``); the OKF-import half is new scaffolding
built on top of it using ``ConceptService.import_okf``.
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
from uuid import uuid4

import pytest
import yaml
from agent_session_tools.context.public import open_context
from agent_session_tools.context.scope import visibility_sql

from session_weaver.concepts import ConceptService

_EVIDENCE_SCHEMA = "session-weaver.concept-projection-baseline"
_EVIDENCE_VERSION = 2

_DEFAULT_SESSIONS_DB = Path.home() / ".config" / "studyloop" / "sessions.db"
_DEFAULT_OKF_STORE = (
    Path.home() / ".local" / "share" / "sessionweaver" / "poc-storage-decision" / "okf-store"
)

_BASELINE_KEYS = frozenset(
    {
        "evidence_schema",
        "evidence_version",
        "captured_at_utc",
        "source",
        "import_counts",
        "projection",
        "aggregate_projection_sha256",
        "trust_labels",
        "timings_seconds",
        "cleanup_ok",
        "source_sentinels_unchanged",
        "okf_source_sentinel_unchanged",
    }
)
_PROJECTION_RUN_KEYS = frozenset(
    {
        "selected",
        "rendered",
        "unchanged",
        "created",
        "replaced",
        "deleted",
        "conflicts",
        "skipped_unavailable",
        "skipped_retired",
    }
)


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


def _create_online_backup(source: Path, backup: Path) -> tuple[dict[str, int], str]:
    with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as source_conn:
        source_conn.execute("PRAGMA query_only = ON")
        source_conn.execute("BEGIN")
        try:
            sentinels = _sentinels(source_conn)
            with closing(sqlite3.connect(backup)) as backup_conn:
                source_conn.backup(backup_conn)
            return sentinels, _sha256_file(backup)
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


def _current_standings(conn: sqlite3.Connection) -> dict[str, str]:
    """Independently derive current standing per concept.

    Uses the same reviewed deterministic tie-break already relied on by
    projection.py/concepts.py (retired > accepted > proposed, then logical
    clock, then id) -- applied fresh here directly against
    context_concept_events, not by calling into projection.py.
    """
    return {
        row[0]: row[1]
        for row in conn.execute(
            """WITH ranked AS (
                 SELECT concept_id, standing,
                   row_number() OVER (
                     PARTITION BY concept_id
                     ORDER BY CASE standing WHEN 'retired' THEN 2
                                            WHEN 'accepted' THEN 1 ELSE 0 END DESC,
                              logical_time DESC, origin_instance DESC, origin_seq DESC, id DESC
                   ) AS position
                 FROM context_concept_events
               )
               SELECT concept_id, standing FROM ranked WHERE position=1"""
        )
    }


def _independent_expected(db: Path) -> tuple[int, int, int]:
    """Independently derive (expected_rendered, bound_count, legacy_count).

    Joins context_concepts directly with the upstream visibility_sql, rather
    than calling projection.py's own selection code: current standing !=
    retired; legacy source_session_id not null; session visible. Matches the
    brief's instruction to cross-check against "the upstream visibility SQL"
    (the same audited primitive production code uses) without going through
    _capture_snapshot/project_concepts at all.
    """
    with open_context(db, project=None) as context:
        standings = _current_standings(context.conn)
        clause, params = visibility_sql(
            context.conn, "s.id", policy=context.policy, scope=context.scope
        )
        rows = context.conn.execute(
            "SELECT id, binding_state, assertion_id, source_session_id FROM context_concepts"
        ).fetchall()
        expected = 0
        bound_count = 0
        legacy_count = 0
        for concept_id, binding_state, assertion_id, source_session_id in rows:
            if standings.get(concept_id) == "retired":
                continue
            if binding_state == "bound":
                if assertion_id is not None and context._assertion(assertion_id) is not None:
                    expected += 1
                    bound_count += 1
                continue
            if source_session_id is None:
                continue
            visible = context.conn.execute(
                "SELECT 1 FROM sessions s WHERE s.id=? AND " + clause,
                (source_session_id, *params),
            ).fetchone()
            if visible is not None:
                expected += 1
                legacy_count += 1
        return expected, bound_count, legacy_count


def _validate_baseline_schema(evidence: Mapping[str, Any]) -> None:
    """Structural schema check for concept-projection-baseline.json.

    No concept IDs, filenames, paths, prose, tags or session IDs may appear
    anywhere in the document.
    """
    assert set(evidence) == _BASELINE_KEYS
    assert evidence["evidence_schema"] == _EVIDENCE_SCHEMA
    assert evidence["evidence_version"] == _EVIDENCE_VERSION
    assert isinstance(evidence["captured_at_utc"], str) and evidence["captured_at_utc"]

    source = evidence["source"]
    assert set(source) == {
        "session_count",
        "message_count",
        "okf_markdown_files",
    }
    for key in source:
        assert isinstance(source[key], int) and source[key] >= 0

    import_counts = evidence["import_counts"]
    assert set(import_counts) == {
        "scanned",
        "parsed",
        "legacy_unbound",
        "oversized_evidence",
    }
    for key in import_counts:
        assert isinstance(import_counts[key], int) and import_counts[key] >= 0

    projection = evidence["projection"]
    assert set(projection) == {"run1", "run2", "post_retire"}
    for run in projection.values():
        assert set(run) == _PROJECTION_RUN_KEYS
        for key in run:
            assert isinstance(run[key], int) and run[key] >= 0

    assert isinstance(evidence["aggregate_projection_sha256"], str)
    assert len(evidence["aggregate_projection_sha256"]) == 64

    trust_labels = evidence["trust_labels"]
    assert set(trust_labels) == {"model_proposed", "machine_confirmed", "absent"}
    for key in trust_labels:
        assert isinstance(trust_labels[key], int) and trust_labels[key] >= 0

    timings = evidence["timings_seconds"]
    assert set(timings) == {"okf_import", "run1", "run2", "post_retire"}
    for key in timings:
        assert isinstance(timings[key], (int, float)) and timings[key] >= 0

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


@pytest.mark.live
def test_real_corpus_projection_proof() -> None:
    """Disposable real-corpus proof: import + project + rerun + retire + rerun.

    Mutates only a disposable SQLite Online Backup and a private tmp
    projection directory. The real database and the OKF source tree are
    never written to.
    """
    if not _DEFAULT_SESSIONS_DB.is_file():
        pytest.skip(f"no live database at {_DEFAULT_SESSIONS_DB}")
    if not _DEFAULT_OKF_STORE.is_dir():
        pytest.skip(f"no live OKF store at {_DEFAULT_OKF_STORE}")

    okf_hash_before = _sha256_tree(_DEFAULT_OKF_STORE)
    okf_file_count = sum(1 for _ in _DEFAULT_OKF_STORE.rglob("*.md"))

    backup_dir = Path(tempfile.mkdtemp(prefix="session-weaver-projection-live-"))
    backup_path = backup_dir / "sessions.db"
    home_dir = backup_dir / "home"
    home_dir.mkdir()
    config_path = backup_dir / "config.yaml"
    out = Path(f"/tmp/session-weaver-a3b2-live-{uuid4().hex}")

    old_env = {
        key: os.environ.get(key)
        for key in ("HOME", "STUDYLOOP_CONFIG", "STUDYLOOP_DB", "DATABASE_PATH")
    }
    before_sentinels: dict[str, int] | None = None
    try:
        before_sentinels, _ = _create_online_backup(_DEFAULT_SESSIONS_DB, backup_path)

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

        # Task A3c: an evidence body over MAX_BODY_CHARS for a claimed session no
        # longer aborts the whole import transaction (concepts.py's
        # _EvidenceResolver degrades that one record to legacy_unbound /
        # oversized_evidence and continues). The full, unfiltered OKF store is
        # therefore imported directly and read-only; nothing is pre-excluded.
        started = perf_counter()
        import_report = service.import_okf(
            _DEFAULT_OKF_STORE,
            actor="a3c-fix-live-proof",
        )
        import_seconds = perf_counter() - started
        assert import_report.scanned == (
            import_report.parsed
            + import_report.invalid_yaml
            + import_report.invalid_schema
            + import_report.unsafe_path
        )
        assert import_report.scanned == okf_file_count
        assert import_report.parsed == (
            import_report.duplicate_content
            + import_report.already_present
            + import_report.bound
            + import_report.legacy_unbound
        )
        assert import_report.legacy_unbound == (
            import_report.missing_session
            + import_report.no_visible_evidence
            + import_report.no_exact_match
            + import_report.ambiguous_match
            + import_report.oversized_evidence
        )
        assert import_report.oversized_evidence >= 1
        assert import_report.write_failures == 0

        started = perf_counter()
        run1 = service.project(out)
        run1_seconds = perf_counter() - started
        assert run1.status == "ok"
        run1_manifest = (out / ".session-weaver-projection-manifest.json").read_bytes()

        expected_rendered, bound_count, legacy_count = _independent_expected(backup_path)
        assert run1.rendered == expected_rendered > 0
        assert run1.selected == run1.rendered + run1.skipped_unavailable + run1.skipped_retired
        assert bound_count == import_report.bound

        started = perf_counter()
        run2 = service.project(out)
        run2_seconds = perf_counter() - started
        assert run2.status == "ok"
        assert run2.unchanged == run1.rendered
        assert run2.writes == 0
        run2_manifest = (out / ".session-weaver-projection-manifest.json").read_bytes()
        assert run2_manifest == run1_manifest

        manifest = json.loads(run1_manifest)
        retire_name = next(iter(sorted(manifest)))
        retire_concept_id = manifest[retire_name]["concept_id"]
        assert (
            service.transition(
                retire_concept_id,
                "retired",
                actor="owner",
                reason="a3b2-fix-live-proof",
            ).writes
            == 1
        )

        started = perf_counter()
        post_retire = service.project(out)
        post_retire_seconds = perf_counter() - started
        assert post_retire.status == "ok"
        assert post_retire.deleted == 1
        assert post_retire.rendered == run1.rendered - 1
        post_retire_manifest = (out / ".session-weaver-projection-manifest.json").read_bytes()
        post_retire_names = set(json.loads(post_retire_manifest))
        assert retire_name not in post_retire_names
        assert set(manifest) - {retire_name} <= post_retire_names | {
            ".session-weaver-projection.json"
        }

        source_session_row = _source_session_row(backup_path, retire_concept_id)
        assert source_session_row is not None

        trust_labels = {
            "model_proposed": run1.rendered,
            "machine_confirmed": bound_count,
            "absent": legacy_count,
        }
        aggregate_hash = hashlib.sha256(
            b"".join(sorted((run1_manifest, run2_manifest, post_retire_manifest)))
        ).hexdigest()

        captured_at_utc = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        source_counts = {
            "session_count": before_sentinels["session_count"],
            "message_count": before_sentinels["message_count"],
            "okf_markdown_files": okf_file_count,
        }
        import_counts = {
            "scanned": import_report.scanned,
            "parsed": import_report.parsed,
            "legacy_unbound": import_report.legacy_unbound,
            "oversized_evidence": import_report.oversized_evidence,
        }
        projection_counts = {
            "run1": {key: getattr(run1, key) for key in _PROJECTION_RUN_KEYS},
            "run2": {key: getattr(run2, key) for key in _PROJECTION_RUN_KEYS},
            "post_retire": {key: getattr(post_retire, key) for key in _PROJECTION_RUN_KEYS},
        }
        timings_seconds = {
            "okf_import": round(import_seconds, 6),
            "run1": round(run1_seconds, 6),
            "run2": round(run2_seconds, 6),
            "post_retire": round(post_retire_seconds, 6),
        }
    finally:
        cleanup_errors: list[str] = []
        for target in (out, backup_dir):
            try:
                shutil.rmtree(target, ignore_errors=False)
            except OSError as exc:
                cleanup_errors.append(type(exc).__name__)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # Only reached if the try block above completed without raising.
    assert before_sentinels is not None
    after_sentinels = _read_sentinels(_DEFAULT_SESSIONS_DB)
    okf_hash_after = _sha256_tree(_DEFAULT_OKF_STORE)

    evidence: dict[str, Any] = {
        "evidence_schema": _EVIDENCE_SCHEMA,
        "evidence_version": _EVIDENCE_VERSION,
        "captured_at_utc": captured_at_utc,
        "source": source_counts,
        "import_counts": import_counts,
        "projection": projection_counts,
        "aggregate_projection_sha256": aggregate_hash,
        "trust_labels": trust_labels,
        "timings_seconds": timings_seconds,
        "cleanup_ok": not cleanup_errors,
        "source_sentinels_unchanged": after_sentinels == before_sentinels,
        "okf_source_sentinel_unchanged": okf_hash_after == okf_hash_before,
    }

    _validate_baseline_schema(evidence)
    assert evidence["cleanup_ok"] is True
    assert evidence["source_sentinels_unchanged"] is True

    baseline_path = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "data"
        / "concept-projection-baseline.json"
    )
    _write_baseline_evidence(evidence, baseline_path)


def _source_session_row(db: Path, concept_id: str) -> tuple[Any, ...] | None:
    """Read-only helper: fetch the source session row referenced by one concept."""
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT source_session_id FROM context_concepts WHERE id=?", (concept_id,)
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return conn.execute("SELECT id FROM sessions WHERE id=?", (row[0],)).fetchone()


def test_concept_projection_baseline_matches_documented_schema() -> None:
    """Non-live: the committed baseline file must match the sanitized schema."""
    baseline_path = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "data"
        / "concept-projection-baseline.json"
    )
    if not baseline_path.is_file():
        pytest.skip("docs/data/concept-projection-baseline.json has not been captured yet")
    evidence = json.loads(baseline_path.read_text(encoding="utf-8"))
    _validate_baseline_schema(evidence)
