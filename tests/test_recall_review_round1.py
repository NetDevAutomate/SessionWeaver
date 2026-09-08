"""Regression coverage for the five Task A4 review-round-1 findings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

import pytest
from agent_session_tools.context.provenance import Origin
from agent_session_tools.context.store import ContextStore, NativeSource

from session_weaver.cli import main
from session_weaver.concepts import ConceptService
from session_weaver.recall import recall

_NOW = "2026-09-08T12:00:00+00:00"
_LATER = "2026-09-08T13:00:00+00:00"
_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "recall-contract.json"


class ProductionStore(Protocol):
    conn: sqlite3.Connection
    db_path: Path


def _capture(store: ProductionStore, body: str, *, key: str) -> None:
    ContextStore(store.conn).capture(
        NativeSource(
            session_id="fixture-session-1",
            native_key=key,
            harness="fixture",
            native_kind="message:user",
            native_locator=f"fixture://fixture-session-1/{key}",
            parser_version="recall-review-round1-v1",
            machine_id="fixture-machine",
            body=body,
            origin=Origin.CONVERSATION,
            recorded_at=_NOW,
        )
    )


def _add_bound_concept(
    store: ProductionStore,
    *,
    term: str,
    title: str,
    evidence: str,
) -> str:
    _capture(store, evidence, key=hashlib.sha256(evidence.encode()).hexdigest()[:16])
    report = ConceptService(store.db_path, now=lambda: _NOW).winddown(
        "fixture-session-1",
        {
            "concepts": [
                {
                    "type": "Finding",
                    "title": title,
                    "description": f"{term} statement",
                    "tags": ["recall", "round1"],
                    "confidence": 0.9,
                    "quotes": [{"quote": evidence}],
                }
            ]
        },
        actor="model",
    )
    return report.concept_ids[0]


def _add_session(store: ProductionStore, session_id: str) -> None:
    store.conn.execute(
        "INSERT INTO sessions(id,source,project_path,updated_at) VALUES (?,?,?,?)",
        (session_id, "fixture", f"/proj/{session_id}", _NOW),
    )
    store.conn.commit()


def _add_messages(
    store: ProductionStore,
    rows: list[tuple[str, str, str, str]],
) -> None:
    store.conn.executemany(
        "INSERT INTO messages(id,session_id,role,content,timestamp) VALUES (?,?,'user',?,?)",
        rows,
    )
    store.conn.commit()


def test_session_fts_storage_failure_maps_to_sanitized_cli_exit_one(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    production_store.conn.execute("DROP TABLE messages_fts")
    production_store.conn.commit()

    exit_code = main(
        ["recall", "storagefailureterm", "--db", str(production_store.db_path), "--json"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "command": "recall",
        "error": "operation failed",
        "writes": 0,
    }


def test_concept_ranking_pages_past_more_than_200_unauthorized_matches(
    production_store: ProductionStore,
) -> None:
    term = "conceptcapstarvationterm"
    concept_id = _add_bound_concept(
        production_store,
        term=term,
        title="Concept authorization cap",
        evidence="concept authorization cap evidence",
    )
    first_rowid = int(
        production_store.conn.execute(
            "SELECT COALESCE(MAX(rowid),0)+1 FROM context_concept_fts"
        ).fetchone()[0]
    )
    production_store.conn.executemany(
        "INSERT INTO context_concept_fts(rowid,title,statement,tags,kind,concept_id)"
        " VALUES (?,?,?,?,?,?)",
        [
            (
                first_rowid + index,
                "Concept authorization cap",
                f"{term} statement",
                "recall round1",
                "Finding",
                "",
            )
            for index in range(200)
        ],
    )
    production_store.conn.commit()

    report = recall(production_store.db_path, term, k=1)

    assert [hit.concept_id for hit in report.concepts] == [concept_id]


def test_session_ranking_excludes_cited_sessions_before_limiting(
    production_store: ProductionStore,
) -> None:
    term = "excludedcapstarvationterm"
    _add_bound_concept(
        production_store,
        term=term,
        title="Excluded session cap",
        evidence="excluded session cap evidence",
    )
    _add_messages(
        production_store,
        [(f"excluded-cap-{index:03d}", "fixture-session-1", term, _LATER) for index in range(300)]
        + [("excluded-cap-eligible", "fixture-session-2", term, _NOW)],
    )

    report = recall(production_store.db_path, term, k=1)

    assert [hit.session_id for hit in report.sessions] == ["fixture-session-2"]


def test_session_ranking_deduplicates_before_limiting(
    production_store: ProductionStore,
) -> None:
    term = "duplicatecapstarvationterm"
    _add_messages(
        production_store,
        [(f"duplicate-cap-{index:03d}", "fixture-session-1", term, _LATER) for index in range(300)]
        + [("duplicate-cap-eligible", "fixture-session-2", term, _NOW)],
    )

    report = recall(production_store.db_path, term, k=2)

    assert [hit.session_id for hit in report.sessions] == [
        "fixture-session-1",
        "fixture-session-2",
    ]


def test_session_ranking_has_stable_session_and_preview_tie_breakers(
    production_store: ProductionStore,
) -> None:
    term = "sessionstabletieterm"
    _add_session(production_store, "round1-tie-z")
    _add_session(production_store, "round1-tie-a")
    _add_messages(
        production_store,
        [
            ("round1-tie-z-message", "round1-tie-z", f"{term} preview-z", _NOW),
            ("round1-tie-message-z", "round1-tie-a", f"{term} preview-z", _NOW),
            ("round1-tie-message-a", "round1-tie-a", f"{term} preview-a", _NOW),
        ],
    )

    report = recall(production_store.db_path, term, k=2)

    assert [hit.session_id for hit in report.sessions] == ["round1-tie-a", "round1-tie-z"]
    assert report.sessions[0].preview == f"{term} preview-a"


def test_recall_cli_json_is_the_unmodified_frozen_report_contract(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "recall",
                "round1clinonsenseterm",
                "--db",
                str(production_store.db_path),
                "--json",
            ]
        )
        == 0
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    schema = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))

    assert set(payload) == set(schema["required"])
    assert set(payload) <= set(schema["properties"])
