"""Safety harness for ontology acceptance on disposable SQLite Online Backups."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from .ontology import (
    EXTRACTION_VERSION,
    OntologyBuildResult,
    OntologyStatus,
    ontology_status,
    rebuild_ontology,
)

_EVIDENCE_SCHEMA = "session-weaver.ontology-tier1-baseline"
_EVIDENCE_VERSION = 1
_MAX_COLD_REBUILD_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class _SourceSentinels:
    schema_version: int
    user_version: int
    session_count: int
    message_count: int
    max_updated_at: str | None
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _sentinels_from_connection(
    conn: sqlite3.Connection,
    *,
    sha256: str,
) -> _SourceSentinels:
    return _SourceSentinels(
        schema_version=int(conn.execute("PRAGMA schema_version").fetchone()[0]),
        user_version=int(conn.execute("PRAGMA user_version").fetchone()[0]),
        session_count=int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
        message_count=int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
        max_updated_at=conn.execute("SELECT MAX(updated_at) FROM sessions").fetchone()[0],
        sha256=sha256,
    )


def _read_source_sentinels(source: Path) -> _SourceSentinels:
    source_hash = _sha256(source)
    with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        try:
            return _sentinels_from_connection(conn, sha256=source_hash)
        finally:
            conn.rollback()


def _create_online_backup(source: Path, backup: Path) -> _SourceSentinels:
    source_hash = _sha256(source)
    with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as source_conn:
        source_conn.execute("PRAGMA query_only = ON")
        source_conn.execute("BEGIN")
        try:
            sentinels = _sentinels_from_connection(source_conn, sha256=source_hash)
            with closing(sqlite3.connect(backup)) as backup_conn:
                source_conn.backup(backup_conn)
        finally:
            source_conn.rollback()
    return sentinels


def _timed_rebuild(
    conn: sqlite3.Connection,
    *,
    incremental: bool,
) -> tuple[OntologyBuildResult, float]:
    started = perf_counter()
    result = rebuild_ontology(conn, incremental=incremental)
    return result, round(perf_counter() - started, 6)


def _validate_acceptance(
    first: OntologyBuildResult,
    first_seconds: float,
    second: OntologyBuildResult,
    incremental: OntologyBuildResult,
    status: OntologyStatus,
) -> None:
    if first.logical_hash != second.logical_hash:
        raise RuntimeError("full rebuild logical hashes differ")
    if first_seconds > _MAX_COLD_REBUILD_SECONDS:
        raise RuntimeError("cold full rebuild exceeded five seconds")
    if incremental.logical_hash != second.logical_hash:
        raise RuntimeError("incremental no-op changed the logical hash")
    if incremental.mode != "incremental" or incremental.fallback_reason is not None:
        raise RuntimeError("incremental no-op unexpectedly fell back")
    if not status.healthy:
        raise RuntimeError("ontology status is unhealthy")
    if status.coverage_ratio < 0.99 or status.missing_sessions:
        raise RuntimeError("ontology session coverage is below acceptance")
    if any(
        (
            status.orphan_session_individuals,
            status.orphan_structural_rows,
            status.foreign_key_violations,
            status.domain_range_violations,
        )
    ):
        raise RuntimeError("ontology integrity diagnostics are nonzero")


def _build_evidence(
    *,
    source: _SourceSentinels,
    backup_snapshot_hash: str,
    backup_final_hash: str,
    first: OntologyBuildResult,
    first_seconds: float,
    second: OntologyBuildResult,
    second_seconds: float,
    incremental: OntologyBuildResult,
    incremental_seconds: float,
    status: OntologyStatus,
) -> dict[str, Any]:
    counts = second.counts
    return {
        "evidence_schema": _EVIDENCE_SCHEMA,
        "evidence_version": _EVIDENCE_VERSION,
        "captured_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "extraction_version": EXTRACTION_VERSION,
        "source": {
            "sha256": source.sha256,
            "schema_version": source.schema_version,
            "user_version": source.user_version,
            "session_count": source.session_count,
            "message_count": source.message_count,
        },
        "backup": {
            "sha256": backup_snapshot_hash,
            "post_rebuild_sha256": backup_final_hash,
        },
        "counts": {
            "classes": counts.classes,
            "properties": counts.properties,
            "structural": counts.structural,
            "individuals": counts.individuals,
            "relations": counts.relations,
        },
        "coverage": {
            "covered_sessions": status.covered_sessions,
            "missing_sessions": status.missing_sessions,
            "coverage_ratio": status.coverage_ratio,
        },
        "integrity": {
            "orphan_session_individuals": status.orphan_session_individuals,
            "orphan_structural_rows": status.orphan_structural_rows,
            "foreign_key_violations": status.foreign_key_violations,
            "domain_range_violations": status.domain_range_violations,
        },
        "first_full_rebuild": {
            "logical_hash": first.logical_hash,
            "elapsed_seconds": first_seconds,
        },
        "second_full_rebuild": {
            "logical_hash": second.logical_hash,
            "elapsed_seconds": second_seconds,
        },
        "incremental_rebuild": {
            "logical_hash": incremental.logical_hash,
            "elapsed_seconds": incremental_seconds,
            "mode": incremental.mode,
            "fallback_reason": incremental.fallback_reason,
        },
        "status": {
            "healthy": status.healthy,
            "coverage_at_least_99_percent": status.coverage_ratio >= 0.99,
            "extraction_version_matches": status.extraction_version_matches,
            "source_counts_match": status.source_counts_match,
            "fresh": status.fresh,
            "hash_matches": status.hash_matches,
        },
        "source_sentinels_unchanged": True,
    }


def _delete_backup(backup: Path) -> None:
    for suffix in ("", "-journal", "-shm", "-wal"):
        Path(f"{backup}{suffix}").unlink(missing_ok=True)


def run_live_copy_acceptance(
    source_path: Path,
    *,
    _backup_dir: Path = Path("/tmp"),
) -> dict[str, Any]:
    """Exercise rebuild/status only on an Online Backup and return sanitized evidence."""
    source = source_path.expanduser()
    if not source.is_file():
        raise RuntimeError("explicit ontology source is not a file")

    file_descriptor, backup_name = tempfile.mkstemp(
        prefix="session-weaver-ontology-",
        suffix=".db",
        dir=_backup_dir,
    )
    os.close(file_descriptor)
    backup = Path(backup_name)
    try:
        before = _create_online_backup(source, backup)
        backup_snapshot_hash = _sha256(backup)
        with closing(sqlite3.connect(backup)) as conn:
            first, first_seconds = _timed_rebuild(conn, incremental=False)
            second, second_seconds = _timed_rebuild(conn, incremental=False)
            incremental, incremental_seconds = _timed_rebuild(conn, incremental=True)
            status = ontology_status(conn)
        backup_final_hash = _sha256(backup)

        _validate_acceptance(first, first_seconds, second, incremental, status)
        after = _read_source_sentinels(source)
        if after != before:
            raise RuntimeError("source sentinels changed during ontology acceptance")

        return _build_evidence(
            source=before,
            backup_snapshot_hash=backup_snapshot_hash,
            backup_final_hash=backup_final_hash,
            first=first,
            first_seconds=first_seconds,
            second=second,
            second_seconds=second_seconds,
            incremental=incremental,
            incremental_seconds=incremental_seconds,
            status=status,
        )
    finally:
        _delete_backup(backup)


def write_baseline_evidence(evidence: Mapping[str, Any], output: Path) -> None:
    """Write one deterministic sanitized baseline JSON document."""
    output.write_text(
        json.dumps(dict(evidence), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
