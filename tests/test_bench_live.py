"""Opt-in A6 full-corpus benchmark on a disposable SQLite Online Backup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from session_weaver.bench import (
    aggregate_evidence,
    audit_gold,
    default_gold_path,
    load_gold,
    run_benchmark,
    validate_gold,
    write_outputs,
)
from session_weaver.concepts import ConceptService
from session_weaver.ontology import rebuild_ontology

_SOURCE = Path.home() / ".config" / "studyloop" / "sessions.db"
_OKF = Path.home() / ".local" / "share" / "sessionweaver" / "poc-storage-decision" / "okf-store"
_EXPORTER = Path.home() / ".local" / "bin" / "session-export"
_ROOT = Path(__file__).resolve().parent.parent
_BASELINE = _ROOT / "docs" / "data" / "bench-baseline-phase-a.json"
_CONTRACT = _ROOT / "docs" / "data" / "bench-contract.json"
_ARTIFACTS = Path("/tmp/session-weaver-a6-phase-a")


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _sentinels(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(_read_only_uri(path), uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        return {
            "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "sessions": int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
            "messages": int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
            "evidence": int(conn.execute("SELECT COUNT(*) FROM context_evidence").fetchone()[0]),
        }


def _online_backup(source: Path, backup: Path) -> dict[str, int]:
    with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as source_conn:
        source_conn.execute("PRAGMA query_only=ON")
        source_conn.execute("BEGIN")
        try:
            before = {
                "user_version": int(source_conn.execute("PRAGMA user_version").fetchone()[0]),
                "sessions": int(source_conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
                "messages": int(source_conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
                "evidence": int(
                    source_conn.execute("SELECT COUNT(*) FROM context_evidence").fetchone()[0]
                ),
            }
            with closing(sqlite3.connect(backup)) as backup_conn:
                source_conn.backup(backup_conn)
            return before
        finally:
            source_conn.rollback()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\n")
    return digest.hexdigest()


def _export_counts(output: str) -> dict[str, int]:
    counters: dict[str, int] = {}
    for key in ("added", "updated", "skipped", "empty"):
        match = re.search(rf"^\s*{key}:\s*(\d+)", output, re.MULTILINE)
        assert match is not None, f"production exporter output omitted {key} counter"
        counters[key] = int(match.group(1))
    return counters


def _import_counts(payload: dict[str, Any]) -> dict[str, int]:
    keys = (
        "scanned",
        "parsed",
        "imported",
        "bound",
        "legacy_unbound",
        "already_present",
        "write_failures",
        "writes",
    )
    return {key: int(payload[key]) for key in keys}


@pytest.mark.live
def test_real_corpus_benchmark_on_online_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Export -> ontology parity -> OKF import -> audit -> benchmark, never on source."""
    if not _SOURCE.is_file():
        pytest.skip(f"no live database at {_SOURCE}")
    if not _OKF.is_dir():
        pytest.skip(f"no OKF corpus at {_OKF}")
    if not _EXPORTER.is_file():
        pytest.skip(f"no production exporter at {_EXPORTER}")

    okf_before = _tree_sha256(_OKF)
    temp_root = Path(tempfile.mkdtemp(prefix="session-weaver-a6-"))
    backup = temp_root / "sessions.db"
    config = temp_root / "config.yaml"
    before = _online_backup(_SOURCE, backup)
    config.write_text(
        yaml.safe_dump(
            {
                "memory": {"default_scope": "unclassified", "projects": {}},
                "database": {
                    "path": str(backup),
                    "archive_path": str(temp_root / "archive.db"),
                    "backup_dir": str(temp_root / "backups"),
                },
                "logging": {"path": str(temp_root / "session-tools.log")},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STUDYLOOP_CONFIG", str(config))
    monkeypatch.delenv("STUDYLOOP_DB", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("SESSION_CONTEXT_SCOPE", raising=False)

    try:
        export_started = perf_counter()
        completed = subprocess.run(
            [str(_EXPORTER)],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        export_seconds = perf_counter() - export_started
        export_counts = _export_counts(completed.stdout + completed.stderr)

        ontology_started = perf_counter()
        with closing(sqlite3.connect(backup)) as conn:
            ontology_result = rebuild_ontology(conn)
        ontology_seconds = perf_counter() - ontology_started

        import_started = perf_counter()
        import_report = ConceptService(backup).import_okf(_OKF, actor="a6-live-benchmark")
        import_seconds = perf_counter() - import_started
        import_payload = import_report.to_dict()
        assert import_payload["write_failures"] == 0
        assert import_payload["parsed"] == 2033

        gold = load_gold(default_gold_path())
        validate_gold(gold)
        gold_audit = audit_gold(backup, gold)
        assert gold_audit["verified_questions"] == 25
        assert gold_audit["failures"] == []

        report = run_benchmark(backup, k=5)
        contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
        Draft202012Validator(contract).validate(report)
        assert report["directional_paraphrase"]["corpus_verified"] >= 40

        if _ARTIFACTS.exists():
            shutil.rmtree(_ARTIFACTS)
        write_outputs(report, _ARTIFACTS)

        live_evaluation = {
            "backup_method": "sqlite_online_backup",
            "config_default_scope": "unclassified",
            "export_run": export_counts,
            "ontology": {
                "mode": ontology_result.mode,
                "counts": asdict(ontology_result.counts),
                "logical_sha256": ontology_result.logical_hash,
            },
            "okf_import": _import_counts(import_payload),
            "gold_audit": {
                key: gold_audit[key]
                for key in (
                    "questions",
                    "verified_questions",
                    "gold_sessions",
                    "existing_sessions",
                    "literal_verified_sessions",
                )
            },
            "source_sentinels_before": before,
            "source_sentinels_unchanged": False,
            "okf_source_unchanged": False,
            "timings_seconds": {
                "export": round(export_seconds, 6),
                "ontology": round(ontology_seconds, 6),
                "import_okf": round(import_seconds, 6),
            },
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=False)

    after = _sentinels(_SOURCE)
    okf_after = _tree_sha256(_OKF)
    live_evaluation["source_sentinels_unchanged"] = after == before
    live_evaluation["okf_source_unchanged"] = okf_after == okf_before
    assert live_evaluation["source_sentinels_unchanged"] is True
    assert live_evaluation["okf_source_unchanged"] is True

    baseline = aggregate_evidence(report, live_evaluation=live_evaluation)
    serialized = json.dumps(baseline, sort_keys=True)
    assert str(Path.home()) not in serialized
    assert "session_id" not in serialized
    assert '"question":' not in serialized.lower()
    assert "What is the SHA-256" not in serialized
    _BASELINE.write_text(
        json.dumps(baseline, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def test_committed_benchmark_baseline_is_aggregate_and_sanitized() -> None:
    if not _BASELINE.is_file():
        pytest.skip("A6 live benchmark baseline has not been captured yet")
    baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
    serialized = json.dumps(baseline, sort_keys=True)

    assert baseline["schema"] == "session-weaver.benchmark.v1"
    assert baseline["k"] == 5
    assert baseline["eligibility"]["all"] == {"total": 25, "K": 11, "P": 8, "R": 6}
    assert str(Path.home()) not in serialized
    assert "session_id" not in serialized
    assert '"question":' not in serialized.lower()
    assert "What is the SHA-256" not in serialized
