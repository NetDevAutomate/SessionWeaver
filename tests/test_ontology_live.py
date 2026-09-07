"""Opt-in ontology acceptance on disposable SQLite Online Backup copies only."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Protocol

import pytest

import session_weaver.ontology as ontology
import session_weaver.ontology_live as ontology_live
from session_weaver.ontology_live import run_live_copy_acceptance, write_baseline_evidence


class ProductionStore(Protocol):
    """The production-schema fixture surface used by safety tests."""

    db_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_artifacts(directory: Path) -> set[str]:
    return {path.name for path in directory.glob("session-weaver-ontology-*")}


def test_live_copy_acceptance_mutates_only_backup_and_returns_sanitized_evidence(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash_before = _sha256(production_store.db_path)
    real_connect = ontology_live.sqlite3.connect
    connections: list[tuple[Any, dict[str, Any]]] = []

    def tracked_connect(
        database: Any,
        *args: Any,
        **kwargs: Any,
    ) -> sqlite3.Connection:
        connections.append((database, kwargs))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(ontology_live.sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(ontology, "_utc_now", lambda: "9998-01-01T00:00:00Z")

    evidence = run_live_copy_acceptance(
        production_store.db_path,
        _backup_dir=tmp_path,
    )
    serialized = json.dumps(evidence, sort_keys=True)
    source_connections = [
        (database, kwargs) for database, kwargs in connections if kwargs.get("uri") is True
    ]

    assert len(source_connections) == 2
    assert all("mode=ro" in str(database) for database, _kwargs in source_connections)

    assert _sha256(production_store.db_path) == source_hash_before
    assert _backup_artifacts(tmp_path) == set()
    assert evidence["evidence_schema"] == "session-weaver.ontology-tier1-baseline"
    assert evidence["evidence_version"] == 1
    assert evidence["source"]["sha256"] == source_hash_before
    assert evidence["source"]["session_count"] == 2
    assert evidence["source"]["message_count"] == 4
    assert (
        evidence["first_full_rebuild"]["logical_hash"]
        == evidence["second_full_rebuild"]["logical_hash"]
    )
    assert evidence["first_full_rebuild"]["elapsed_seconds"] <= 5
    assert (
        evidence["incremental_rebuild"]["logical_hash"]
        == evidence["second_full_rebuild"]["logical_hash"]
    )
    assert evidence["status"]["healthy"] is True
    assert evidence["source_sentinels_unchanged"] is True
    for forbidden in (
        str(production_store.db_path),
        "fixture-project",
        "How does fixture",
        "Through commit_batch",
        "fixture-session-1",
    ):
        assert forbidden not in serialized


def test_live_copy_acceptance_deletes_backup_when_rebuild_fails(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_hash_before = _sha256(production_store.db_path)

    def fail_rebuild(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced rebuild failure")

    monkeypatch.setattr(ontology_live, "rebuild_ontology", fail_rebuild)

    with pytest.raises(RuntimeError, match="forced rebuild failure"):
        run_live_copy_acceptance(production_store.db_path, _backup_dir=tmp_path)

    assert _sha256(production_store.db_path) == source_hash_before
    assert _backup_artifacts(tmp_path) == set()


def test_baseline_writer_is_deterministic_and_contains_no_path(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ontology, "_utc_now", lambda: "9998-01-01T00:00:00Z")
    evidence = run_live_copy_acceptance(production_store.db_path, _backup_dir=tmp_path)
    output = tmp_path / "baseline.json"

    write_baseline_evidence(evidence, output)
    first = output.read_bytes()
    write_baseline_evidence(evidence, output)

    assert output.read_bytes() == first
    assert first.endswith(b"\n")
    assert str(production_store.db_path).encode() not in first


@pytest.mark.live
def test_real_corpus_online_backup_acceptance() -> None:
    source_value = os.environ.get("SESSION_WEAVER_ONTOLOGY_SOURCE")
    if source_value is None:
        pytest.skip("set SESSION_WEAVER_ONTOLOGY_SOURCE explicitly to opt in")

    source = Path(source_value).expanduser()
    evidence = run_live_copy_acceptance(source)

    assert evidence["source_sentinels_unchanged"] is True
    assert (
        evidence["first_full_rebuild"]["logical_hash"]
        == evidence["second_full_rebuild"]["logical_hash"]
    )
    assert evidence["first_full_rebuild"]["elapsed_seconds"] <= 5
    assert (
        evidence["incremental_rebuild"]["logical_hash"]
        == evidence["second_full_rebuild"]["logical_hash"]
    )
    assert evidence["status"]["healthy"] is True
    assert str(source) not in json.dumps(evidence, sort_keys=True)

    evidence_target = os.environ.get("SESSION_WEAVER_ONTOLOGY_EVIDENCE")
    if evidence_target is not None:
        write_baseline_evidence(evidence, Path(evidence_target))
