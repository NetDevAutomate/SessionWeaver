"""Legacy OKF parsing: deterministic, bounded, and content-safe."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import pytest
from agent_session_tools.context.provenance import Origin
from agent_session_tools.context.store import ContextStore, NativeSource

from session_weaver.concepts import ConceptService
from session_weaver.okf import MAX_ERROR_ENTRIES, MAX_OKF_BYTES, _scan_okf

_COUNTER_KEYS = {
    "scanned",
    "parsed",
    "invalid_yaml",
    "invalid_schema",
    "unsafe_path",
    "duplicate_content",
    "already_present",
    "bound",
    "legacy_unbound",
    "missing_session",
    "no_visible_evidence",
    "no_exact_match",
    "ambiguous_match",
    "body_description_mismatch",
    "imported",
    "write_failures",
    "writes",
}


def _okf_bytes(
    *,
    kind: str = "Finding",
    title: str = "Synthetic title",
    description: str = "Synthetic description",
    tags: tuple[str, ...] = ("legacy", "synthetic"),
    confidence: float = 0.9,
    session_id: str = "fixture-session-1",
    actor: str = "fixture-writer/0.1",
    body: str | None = None,
) -> bytes:
    lines = [
        "---",
        f"type: {kind}",
        f"title: {json.dumps(title)}",
        f"description: {json.dumps(description)}",
        f"tags: {json.dumps(tags)}",
        "sources:",
        f"  - resource: sessionweaver://session/{session_id}",
        "    role: transcript",
        "verified:",
        "  status: machine-confirmed",
        f"  by: {actor}",
        f"confidence: {confidence}",
        f"actor: {actor}",
        "---",
        "",
        description if body is None else body,
    ]
    return "\n".join(lines).encode()


def _write(root: Path, relative: str, payload: bytes) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def _error_rows(scan: object) -> list[dict[str, str]]:
    return scan.report.to_dict()["errors"]  # type: ignore[no-any-return,union-attr]


def test_nested_records_use_byte_order_full_body_and_original_bytes_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    later = _okf_bytes(description="short", body="short\n\nfull Markdown continuation")
    earlier = _okf_bytes(title="Earlier", session_id="fixture-session-2")
    _write(root, "z-last.md", later)
    _write(root, "a/first.md", earlier)

    scan = _scan_okf(root)

    assert [record.relative_path for record in scan.records] == ["a/first.md", "z-last.md"]
    assert scan.records[1].statement == "short\n\nfull Markdown continuation"
    assert scan.records[1].description == "short"
    assert scan.records[1].legacy_id == "legacy:" + hashlib.sha256(later).hexdigest()
    assert scan.records[1].source_uri == "sessionweaver://session/fixture-session-1"
    assert scan.records[1].verified_status == "machine-confirmed"
    assert scan.report.scanned == 2
    assert scan.report.parsed == 2
    assert scan.report.body_description_mismatch == 1
    assert scan.report.invalid_yaml == 0
    assert scan.report.invalid_schema == 0
    assert set(scan.report.to_dict()) == _COUNTER_KEYS | {"errors"}


@pytest.mark.parametrize(
    ("name", "payload", "code"),
    [
        (
            "malformed",
            b"---\ntype: Finding\ntitle: [\n---\n\nbody",
            "invalid_yaml",
        ),
        (
            "alias",
            _okf_bytes()
            .replace(
                b'title: "Synthetic title"',
                b'title: &title "Synthetic title"\ndescription: *title',
            )
            .replace(b'description: "Synthetic description"\n', b"", 1),
            "unsafe_yaml",
        ),
        (
            "merge",
            _okf_bytes().replace(
                b"type: Finding",
                b"base: &base {type: Finding}\n<<: *base",
            ),
            "unsafe_yaml",
        ),
        (
            "custom-tag",
            _okf_bytes().replace(
                b'title: "Synthetic title"',
                b'title: !fixture "Synthetic title"',
            ),
            "unsafe_yaml",
        ),
    ],
)
def test_malformed_alias_merge_and_custom_tag_yaml_are_rejected(
    tmp_path: Path,
    name: str,
    payload: bytes,
    code: str,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, f"{name}.md", payload)

    scan = _scan_okf(root)

    assert scan.report.scanned == 1
    assert scan.report.parsed == 0
    assert scan.report.invalid_yaml == 1
    assert scan.report.invalid_schema == 0
    assert _error_rows(scan) == [{"path": f"{name}.md", "code": code, "field": "/"}]


SchemaMutation = Callable[[bytes], bytes]


@pytest.mark.parametrize(
    ("name", "mutate", "field"),
    [
        (
            "missing-actor",
            lambda raw: raw.replace(b"actor: fixture-writer/0.1\n", b"", 1),
            "/actor",
        ),
        (
            "extra-field",
            lambda raw: raw.replace(b"type: Finding", b"extra: value\ntype: Finding"),
            "/extra",
        ),
        (
            "bad-kind",
            lambda raw: raw.replace(b"type: Finding", b"type: Guess"),
            "/type",
        ),
        (
            "blank-title",
            lambda raw: raw.replace(b'title: "NEVER-EMIT-SYNTHETIC-CONTENT"', b'title: "  "'),
            "/title",
        ),
        (
            "bad-tags",
            lambda raw: raw.replace(b'tags: ["legacy", "synthetic"]', b'tags: ["Legacy"]'),
            "/tags",
        ),
        (
            "boolean-confidence",
            lambda raw: raw.replace(b"confidence: 0.9", b"confidence: true"),
            "/confidence",
        ),
        (
            "two-sources",
            lambda raw: raw.replace(
                b"    role: transcript\nverified:",
                b"    role: transcript\n  - resource: sessionweaver://session/other\n"
                b"    role: transcript\nverified:",
            ),
            "/sources",
        ),
        (
            "wrong-role",
            lambda raw: raw.replace(b"role: transcript", b"role: summary"),
            "/sources/0/role",
        ),
        (
            "wrong-resource",
            lambda raw: raw.replace(
                b"sessionweaver://session/fixture-session-1", b"file:///transcript"
            ),
            "/sources/0/resource",
        ),
        (
            "wrong-status",
            lambda raw: raw.replace(b"status: machine-confirmed", b"status: human-confirmed"),
            "/verified/status",
        ),
        (
            "different-verifier",
            lambda raw: raw.replace(b"by: fixture-writer/0.1", b"by: other-writer"),
            "/verified/by",
        ),
    ],
)
def test_exact_writer_schema_is_required_with_content_free_field_errors(
    tmp_path: Path,
    name: str,
    mutate: SchemaMutation,
    field: str,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    private_marker = "NEVER-EMIT-SYNTHETIC-CONTENT"
    payload = mutate(
        _okf_bytes(
            title=private_marker,
            description=private_marker,
            body=private_marker,
        )
    )
    _write(root, f"{name}.md", payload)

    scan = _scan_okf(root)
    encoded = json.dumps(scan.report.to_dict(), sort_keys=True)

    assert scan.report.scanned == 1
    assert scan.report.parsed == 0
    assert scan.report.invalid_schema == 1
    assert scan.report.invalid_yaml == 0
    assert any(row["field"] == field for row in _error_rows(scan))
    assert private_marker not in encoded
    assert "fixture-session-1" not in encoded


def test_symlink_file_directory_and_containment_escape_are_unsafe(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    external_file = _write(outside, "external.md", _okf_bytes())
    _write(root, "safe.md", _okf_bytes(title="Safe"))
    (root / "linked-file.md").symlink_to(external_file)
    (root / "linked-directory").symlink_to(outside, target_is_directory=True)

    scan = _scan_okf(root)

    assert scan.report.scanned == 3
    assert scan.report.parsed == 1
    assert scan.report.unsafe_path == 2
    assert [record.relative_path for record in scan.records] == ["safe.md"]
    assert {(row["path"], row["code"]) for row in _error_rows(scan)} == {
        ("linked-directory", "unsafe_path"),
        ("linked-file.md", "unsafe_path"),
    }


def test_non_utf8_and_oversized_files_are_invalid_before_yaml_parse(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    _write(root, "invalid-utf8.md", b"\xff\xfe")
    _write(root, "oversized.md", b"x" * (MAX_OKF_BYTES + 1))

    scan = _scan_okf(root)

    assert scan.report.scanned == 2
    assert scan.report.parsed == 0
    assert scan.report.invalid_schema == 2
    assert scan.report.invalid_yaml == 0
    assert {(row["path"], row["code"]) for row in _error_rows(scan)} == {
        ("invalid-utf8.md", "invalid_utf8"),
        ("oversized.md", "file_too_large"),
    }


def test_duplicate_bytes_content_and_identity_are_counted_deterministically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    original = _okf_bytes()
    semantically_identical = original.replace(b"type: Finding", b'type: "Finding"')
    _write(root, "a.md", original)
    _write(root, "b.md", original)
    _write(root, "c.md", semantically_identical)

    first = _scan_okf(root)
    second = _scan_okf(root)

    assert first.report.to_dict() == second.report.to_dict()
    assert first.report.scanned == 3
    assert first.report.parsed == 3
    assert first.report.duplicate_content == 2
    assert len(first.records) == 1
    assert first.records[0].relative_path == "a.md"
    assert [(row["path"], row["code"]) for row in _error_rows(first)] == [
        ("b.md", "duplicate_content"),
        ("c.md", "duplicate_content"),
    ]
    assert (
        first.report.scanned
        == first.report.parsed
        + first.report.invalid_yaml
        + first.report.invalid_schema
        + first.report.unsafe_path
    )
    assert first.report.parsed == len(first.records) + first.report.duplicate_content


def test_per_file_errors_are_bounded_without_changing_file_counters(tmp_path: Path) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    total = MAX_ERROR_ENTRIES + 7
    for index in range(total):
        _write(root, f"{index:03d}.md", b"---\ntitle: [\n---\n\nbody")

    scan = _scan_okf(root)

    assert scan.report.scanned == total
    assert scan.report.invalid_yaml == total
    assert len(scan.report.errors) == MAX_ERROR_ENTRIES
    assert [error.relative_path for error in scan.report.errors] == [
        f"{index:03d}.md" for index in range(MAX_ERROR_ENTRIES)
    ]


_NOW = "2026-09-08T12:00:00+00:00"


class ProductionStore(Protocol):
    conn: sqlite3.Connection
    db_path: Path


def _capture(
    store: ProductionStore,
    body: str,
    *,
    session_id: str = "fixture-session-1",
    key: str = "okf-import-evidence",
) -> str:
    return ContextStore(store.conn).capture(
        NativeSource(
            session_id=session_id,
            native_key=key,
            harness="fixture",
            native_kind="message:user",
            native_locator=f"fixture://{session_id}/{key}",
            parser_version="okf-test-v1",
            machine_id="fixture-machine",
            body=body,
            origin=Origin.CONVERSATION,
            recorded_at=_NOW,
        )
    )


def _concept_state(conn: sqlite3.Connection) -> dict[str, object]:
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "context_assertions",
            "context_citations",
            "context_concepts",
            "context_concept_events",
            "context_concept_fts",
        )
    } | {
        "clock": conn.execute(
            "SELECT origin_seq,logical_time FROM context_concept_clock WHERE id=1"
        ).fetchone()
    }


@pytest.mark.parametrize(
    ("classification", "expected_counter"),
    [
        ("unique", "bound"),
        ("missing", "missing_session"),
        ("no-evidence", "no_visible_evidence"),
        ("no-match", "no_exact_match"),
        ("ambiguous", "ambiguous_match"),
    ],
)
def test_dry_run_classifies_full_body_against_visible_evidence_with_zero_writes(
    production_store: ProductionStore,
    tmp_path: Path,
    classification: str,
    expected_counter: str,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    session_id = "fixture-session-1"
    description = "Truncated frontmatter summary"
    body = f"Full canonical body for {classification}; description is not the quote."
    if classification == "unique":
        _capture(production_store, body, key="unique-body")
    elif classification == "missing":
        session_id = "absent-session"
    elif classification == "no-evidence":
        production_store.conn.execute(
            "INSERT INTO context_tombstones VALUES (?,?,?)",
            (session_id, "hide-fixture-session", _NOW),
        )
        production_store.conn.commit()
    elif classification == "ambiguous":
        _capture(production_store, f"{body}\n{body}", key="ambiguous-body")

    _write(
        root,
        "concept.md",
        _okf_bytes(
            title=f"Synthetic {classification}",
            description=description,
            body=body,
            session_id=session_id,
        ),
    )
    service = ConceptService(production_store.db_path, now=lambda: _NOW)
    baseline = _concept_state(production_store.conn)

    report = service.import_okf(root, actor="fixture-importer", dry_run=True)

    assert _concept_state(production_store.conn) == baseline
    assert report.scanned == 1
    assert report.parsed == 1
    assert report.body_description_mismatch == 1
    assert report.imported == 0
    assert report.writes == 0
    assert report.write_failures == 0
    assert report.already_present == 0
    assert getattr(report, expected_counter) == 1
    assert report.bound + report.legacy_unbound == 1
    assert (
        report.missing_session
        + report.no_visible_evidence
        + report.no_exact_match
        + report.ambiguous_match
        == report.legacy_unbound
    )
    assert report.errors == ()


def test_body_outside_safe_citation_limit_stays_legacy_unbound(
    production_store: ProductionStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    body = "x" * 2001
    _capture(production_store, body, key="oversized-quote-body")
    _write(root, "long-body.md", _okf_bytes(description="short", body=body))
    service = ConceptService(production_store.db_path, now=lambda: _NOW)

    report = service.import_okf(root, actor="fixture-importer", dry_run=True)

    assert report.bound == 0
    assert report.legacy_unbound == 1
    assert report.no_exact_match == 1
    assert report.writes == 0


def test_write_import_reuses_safe_bind_and_leaves_historical_trust_proposed(
    production_store: ProductionStore,
    tmp_path: Path,
) -> None:
    from session_weaver.concepts import _ConceptRepository

    root = tmp_path / "okf"
    root.mkdir()
    bound_body = "Full exact body imported through the reviewed safe bind path."
    unbound_body = "No evidence contains this full legacy body."
    _capture(production_store, bound_body, key="write-bound-body")
    bound_bytes = _okf_bytes(
        title="Bound import",
        description="truncated",
        body=bound_body,
    )
    unbound_bytes = _okf_bytes(
        title="Unbound import",
        description=unbound_body,
        body=unbound_body,
    )
    bound_source = _write(root, "a-bound.md", bound_bytes)
    unbound_source = _write(root, "b-unbound.md", unbound_bytes)
    source_receipt = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (bound_source, unbound_source)
    }
    service = ConceptService(production_store.db_path, now=lambda: _NOW)

    dry_run = service.import_okf(root, actor="fixture-importer", dry_run=True)
    result = service.import_okf(root, actor="fixture-importer")

    assert result.scanned == dry_run.scanned == 2
    assert result.parsed == dry_run.parsed == 2
    assert result.bound == dry_run.bound == 1
    assert result.legacy_unbound == dry_run.legacy_unbound == 1
    assert result.no_exact_match == dry_run.no_exact_match == 1
    assert result.body_description_mismatch == dry_run.body_description_mismatch == 1
    assert result.imported == 2
    assert result.write_failures == 0
    assert result.writes == 6
    assert result.errors == ()
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (bound_source, unbound_source)
    } == source_receipt

    conn = production_store.conn
    bound_legacy_id = "legacy:" + hashlib.sha256(bound_bytes).hexdigest()
    unbound_legacy_id = "legacy:" + hashlib.sha256(unbound_bytes).hexdigest()
    roots = conn.execute(
        """SELECT id,binding_state,origin,statement,producer,supersedes_concept_id
           FROM context_concepts ORDER BY id"""
    ).fetchall()
    assert len(roots) == 3
    assert conn.execute(
        """SELECT binding_state,origin,statement,source_uri,producer
           FROM context_concepts WHERE id=?""",
        (unbound_legacy_id,),
    ).fetchone() == (
        "legacy-unbound",
        "legacy-okf",
        unbound_body,
        "sessionweaver://session/fixture-session-1",
        "fixture-writer/0.1",
    )
    successor = conn.execute(
        "SELECT id FROM context_concepts WHERE supersedes_concept_id=?",
        (bound_legacy_id,),
    ).fetchone()
    assert successor is not None
    successor_id = successor[0]
    assertion = conn.execute(
        "SELECT proposed_state,proposed_target,statement FROM context_assertions WHERE id=?",
        (successor_id,),
    ).fetchone()
    assert assertion == ("unknown", None, bound_body)
    citation = conn.execute(
        "SELECT c.start_offset,c.end_offset,c.quote,e.body FROM context_citations c "
        "JOIN context_evidence e ON e.id=c.evidence_id WHERE c.assertion_id=?",
        (successor_id,),
    ).fetchone()
    assert citation is not None
    assert citation[2] == bound_body
    assert citation[3][citation[0] : citation[1]] == bound_body

    repo = _ConceptRepository(conn, now=lambda: _NOW)
    assert repo.current_event(bound_legacy_id)["standing"] == "retired"
    assert repo.current_event(unbound_legacy_id)["standing"] == "proposed"
    assert repo.current_event(unbound_legacy_id)["actor"] == "fixture-importer"
    assert repo.current_event(successor_id)["standing"] == "proposed"
    assert (
        conn.execute(
            "SELECT count(*) FROM context_concept_events WHERE standing='accepted'"
        ).fetchone()[0]
        == 0
    )


def test_reimport_counts_existing_roots_and_successors_without_new_rows(
    production_store: ProductionStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    body = "Idempotent full-body evidence."
    _capture(production_store, body, key="idempotent-body")
    _write(root, "bound.md", _okf_bytes(title="Idempotent bound", body=body))
    _write(
        root,
        "unbound.md",
        _okf_bytes(title="Idempotent unbound", body="No idempotent evidence exists."),
    )
    service = ConceptService(production_store.db_path, now=lambda: _NOW)

    first = service.import_okf(root, actor="fixture-importer")
    baseline = _concept_state(production_store.conn)
    event_ids = production_store.conn.execute(
        "SELECT id FROM context_concept_events ORDER BY id"
    ).fetchall()
    second = service.import_okf(root, actor="fixture-importer")

    assert first.imported == 2
    assert second.parsed == 2
    assert second.already_present == 2
    assert second.imported == 0
    assert second.bound == 0
    assert second.legacy_unbound == 0
    assert second.writes == 0
    assert second.write_failures == 0
    assert _concept_state(production_store.conn) == baseline
    assert (
        production_store.conn.execute(
            "SELECT id FROM context_concept_events ORDER BY id"
        ).fetchall()
        == event_ids
    )


def test_all_records_resolve_before_first_write_in_one_outer_transaction(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    import session_weaver.concepts as concepts_module

    root = tmp_path / "okf"
    root.mkdir()
    for index in range(2):
        body = f"Resolve-before-write evidence {index}."
        _capture(production_store, body, key=f"resolve-first-{index}")
        _write(root, f"{index}.md", _okf_bytes(title=f"Resolve first {index}", body=body))
    service = ConceptService(production_store.db_path, now=lambda: _NOW)
    open_calls: list[bool] = []
    root_counts_during_resolution: list[int] = []
    real_open_context = concepts_module.open_context
    real_visible_sources = concepts_module._EvidenceResolver._visible_sources

    @contextmanager
    def counted_open_context(*args: Any, **kwargs: Any):
        open_calls.append(bool(kwargs.get("write")))
        with real_open_context(*args, **kwargs) as context:
            yield context

    def checked_visible_sources(self: object) -> dict[str, str]:
        resolver = self
        root_counts_during_resolution.append(
            resolver._context.conn.execute(  # type: ignore[attr-defined]
                "SELECT count(*) FROM context_concepts"
            ).fetchone()[0]
        )
        return real_visible_sources(resolver)  # type: ignore[arg-type]

    monkeypatch.setattr(concepts_module, "open_context", counted_open_context)
    monkeypatch.setattr(
        concepts_module._EvidenceResolver,
        "_visible_sources",
        checked_visible_sources,
    )

    result = service.import_okf(root, actor="fixture-importer")

    assert result.imported == 2
    assert result.bound == 2
    assert open_calls == [True]
    assert root_counts_during_resolution == [0, 0]


def test_late_import_failure_rolls_back_every_valid_record_and_returns_sanitized_report(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from session_weaver.concepts import _ConceptRepository

    root = tmp_path / "okf"
    root.mkdir()
    bound_body = "Late rollback exact evidence."
    _capture(production_store, bound_body, key="late-rollback-body")
    _write(root, "a-bound.md", _okf_bytes(title="Rollback bound", body=bound_body))
    _write(
        root,
        "b-unbound.md",
        _okf_bytes(title="Rollback unbound", body="Rollback unmatched body."),
    )
    service = ConceptService(production_store.db_path, now=lambda: _NOW)
    baseline = _concept_state(production_store.conn)

    def fail_after_retirement(self: object, checkpoint: str) -> None:
        if checkpoint == "after_legacy_retired_event":
            raise RuntimeError("synthetic private failure detail")

    monkeypatch.setattr(_ConceptRepository, "_checkpoint", fail_after_retirement)

    result = service.import_okf(root, actor="fixture-importer")

    assert result.bound == 1
    assert result.legacy_unbound == 1
    assert result.imported == 0
    assert result.writes == 0
    assert result.write_failures == 2
    assert [error.to_dict() for error in result.errors] == [
        {"path": "", "code": "write_failed", "field": "/"}
    ]
    assert "synthetic private failure detail" not in json.dumps(result.to_dict())
    assert _concept_state(production_store.conn) == baseline


def test_invalid_files_remain_report_entries_without_aborting_valid_import(
    production_store: ProductionStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    valid = _write(root, "valid.md", _okf_bytes(title="Valid alongside invalid"))
    invalid = _write(root, "invalid.md", b"---\ntitle: [\n---\n\nprivate invalid body")
    source_receipt = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (valid, invalid)
    }
    service = ConceptService(production_store.db_path, now=lambda: _NOW)

    result = service.import_okf(root, actor="fixture-importer")

    assert result.scanned == 2
    assert result.parsed == 1
    assert result.invalid_yaml == 1
    assert result.imported == 1
    assert result.legacy_unbound == 1
    assert result.writes == 1
    assert [error.code for error in result.errors] == ["invalid_yaml"]
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (valid, invalid)
    } == source_receipt


def test_json_escaped_surrogate_pairs_normalize_to_unicode_scalars(tmp_path: Path) -> None:
    root = tmp_path / "okf-surrogate-pair"
    root.mkdir()
    value = "Synthetic emoji 🙂 value"
    _write(root, "paired.md", _okf_bytes(title=value, description=value, body=value))

    scan = _scan_okf(root)

    assert scan.report.parsed == 1
    assert scan.report.invalid_schema == 0
    assert scan.records[0].title == value
    assert scan.records[0].description == value


def test_lone_json_escaped_surrogate_is_invalid_schema_not_a_parser_crash(
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf-lone-surrogate"
    root.mkdir()
    payload = _okf_bytes().replace(b'title: "Synthetic title"', b'title: "\\ud83d"')
    _write(root, "lone.md", payload)

    scan = _scan_okf(root)

    assert scan.report.parsed == 0
    assert scan.report.invalid_schema == 1
    assert any(
        error.code == "invalid_unicode" and error.field == "/title" for error in scan.report.errors
    )


def test_legacy_title_uses_frozen_writer_codepoint_limit_not_prompt_word_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf-writer-title"
    root.mkdir()
    title = "one two three four five six seven eight nine ten eleven twelve thirteen"
    assert len(title) <= 120
    _write(root, "writer-valid.md", _okf_bytes(title=title))

    scan = _scan_okf(root)

    assert scan.report.parsed == 1
    assert scan.report.invalid_schema == 0
    assert scan.records[0].title == title


def test_body_description_mismatch_is_counted_even_when_other_schema_is_invalid(
    tmp_path: Path,
) -> None:
    root = tmp_path / "okf-invalid-schema-mismatch"
    root.mkdir()
    payload = _okf_bytes(description="summary", body="different full body").replace(
        b'tags: ["legacy", "synthetic"]', b'tags: ["Legacy", "synthetic"]'
    )
    _write(root, "invalid-tags.md", payload)

    scan = _scan_okf(root)

    assert scan.report.parsed == 0
    assert scan.report.invalid_schema == 1
    assert scan.report.body_description_mismatch == 1


def test_non_string_yaml_field_names_are_content_free_schema_errors(tmp_path: Path) -> None:
    root = tmp_path / "okf-non-string-key"
    root.mkdir()
    payload = _okf_bytes().replace(b"type: Finding", b"1: hidden\ntype: Finding")
    _write(root, "non-string-key.md", payload)

    scan = _scan_okf(root)

    assert scan.report.parsed == 0
    assert scan.report.invalid_schema == 1
    assert any(
        error.code == "invalid_field_name" and error.field == "/" for error in scan.report.errors
    )
    assert "hidden" not in json.dumps(scan.report.to_dict())
