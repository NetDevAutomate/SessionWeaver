"""Database-writing CLI safety and exit contracts for concepts and OKF import."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

import pytest
from agent_session_tools.context.provenance import Origin
from agent_session_tools.context.store import ContextStore, NativeSource

from session_weaver.cli import main
from session_weaver.concept_schema import _ensure_schema
from session_weaver.concepts import ConceptService, _ConceptRepository
from session_weaver.winddown import MAX_REQUEST_BYTES

_NOW = "2026-09-08T12:00:00+00:00"


class ProductionStore(Protocol):
    conn: sqlite3.Connection
    db_path: Path


def _capture(
    store: ProductionStore,
    body: str,
    *,
    session_id: str = "fixture-session-1",
    key: str = "cli-evidence",
) -> str:
    return ContextStore(store.conn).capture(
        NativeSource(
            session_id=session_id,
            native_key=key,
            harness="fixture",
            native_kind="message:user",
            native_locator=f"fixture://{session_id}/{key}",
            parser_version="concept-cli-test-v1",
            machine_id="fixture-machine",
            body=body,
            origin=Origin.CONVERSATION,
            recorded_at=_NOW,
        )
    )


def _winddown_document(quote: str, *, description: str = "CLI bound concept") -> dict[str, Any]:
    return {
        "concepts": [
            {
                "type": "Finding",
                "title": "CLI exact binding",
                "description": description,
                "tags": ["cli", "session-weaver"],
                "confidence": 0.9,
                "quotes": [{"quote": quote}],
            }
        ]
    }


def _okf_bytes(
    *,
    title: str = "CLI legacy title",
    description: str = "CLI legacy description",
    body: str | None = None,
    session_id: str = "fixture-session-1",
) -> bytes:
    actor = "fixture-writer/0.1"
    return "\n".join(
        [
            "---",
            "type: Finding",
            f"title: {json.dumps(title)}",
            f"description: {json.dumps(description)}",
            'tags: ["cli", "legacy"]',
            "sources:",
            f"  - resource: sessionweaver://session/{session_id}",
            "    role: transcript",
            "verified:",
            "  status: machine-confirmed",
            f"  by: {actor}",
            "confidence: 0.9",
            f"actor: {actor}",
            "---",
            "",
            description if body is None else body,
        ]
    ).encode()


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _seed_legacy(store: ProductionStore, *, statement: str) -> str:
    _ensure_schema(store.conn)
    identity = _ConceptRepository(store.conn, now=lambda: _NOW).seed_legacy(
        original_bytes=f"legacy:{statement}".encode(),
        kind="Finding",
        title="CLI legacy seed",
        statement=statement,
        tags=("cli", "legacy"),
        confidence=0.8,
        source_session_id="fixture-session-1",
        source_uri="sessionweaver://session/fixture-session-1",
        producer="fixture-writer/0.1",
    )
    store.conn.commit()
    return identity


def _json_output(capsys: pytest.CaptureFixture[str], *, error: bool = False) -> dict[str, Any]:
    captured = capsys.readouterr()
    stream = captured.err if error else captured.out
    return json.loads(stream)


def _assert_closed(conn: sqlite3.Connection) -> None:
    try:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
    finally:
        conn.close()


@pytest.mark.parametrize("mode", ("file", "stdin"))
def test_winddown_accepts_exactly_one_bounded_input_source(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    quote = f"CLI {mode} exact evidence"
    _capture(production_store, quote, key=f"winddown-{mode}")
    document = json.dumps(_winddown_document(quote))
    argv = ["winddown", "--session", "fixture-session-1", "--db", str(production_store.db_path)]
    if mode == "file":
        request = _write(tmp_path / "winddown.json", document.encode())
        argv.extend(["--from", str(request)])
    else:
        monkeypatch.setattr(sys, "stdin", io.StringIO(document))
        argv.append("--stdin")

    assert main(argv) == 0
    payload = _json_output(capsys)

    assert payload["command"] == "winddown"
    assert payload["writes"] == 1
    assert len(payload["concept_ids"]) == 1
    assert payload["errors"] == []


def test_winddown_requires_one_input_source_and_rejects_both(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _write(tmp_path / "request.json", b"{}")

    with pytest.raises(SystemExit) as missing:
        main(["winddown", "--session", "fixture-session-1", "--db", str(production_store.db_path)])
    assert missing.value.code == 2
    assert "one of the arguments --from --stdin is required" in capsys.readouterr().err

    with pytest.raises(SystemExit) as both:
        main(
            [
                "winddown",
                "--session",
                "fixture-session-1",
                "--from",
                str(request),
                "--stdin",
                "--db",
                str(production_store.db_path),
            ]
        )
    assert both.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("unsafe_kind", ("oversized", "symlink"))
def test_winddown_rejects_unsafe_input_before_service_or_parse(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unsafe_kind: str,
) -> None:
    if unsafe_kind == "oversized":
        request = _write(tmp_path / "oversized.json", b"x" * (MAX_REQUEST_BYTES + 1))
        expected = "input_too_large"
    else:
        target = _write(tmp_path / "target.json", b"{}")
        request = tmp_path / "linked.json"
        request.symlink_to(target)
        expected = "unsafe_input"

    class UnexpectedService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("unsafe input must be rejected before a service opener")

    monkeypatch.setattr("session_weaver.cli.ConceptService", UnexpectedService, raising=False)

    assert (
        main(
            [
                "winddown",
                "--session",
                "fixture-session-1",
                "--from",
                str(request),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 2
    )
    payload = _json_output(capsys, error=True)
    assert payload["writes"] == 0
    assert payload["errors"][0]["code"] == expected
    assert str(request) not in json.dumps(payload)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["winddown", "--session", "fixture-session-1", "--from", ""], "--from must not be empty"),
        (
            ["winddown", "--session", "fixture-session-1", "--from", "request.json", "--db", ""],
            "--db must not be empty",
        ),
        (
            ["winddown", "--session", "fixture-session-1", "--from", "request.json", "--actor", ""],
            "--actor must not be empty",
        ),
        (
            ["concept", "accept", "legacy:x", "--reason", ""],
            "--reason must not be empty",
        ),
        (
            ["concept", "retire", "", "--reason", "why"],
            "concept id must not be empty",
        ),
        (
            ["concept", "bind", "legacy:x", "--from", "", "--reason", "why"],
            "--from must not be empty",
        ),
        (["concept", "import-okf", ""], "directory must not be empty"),
        (
            ["concept", "import-okf", "okf", "--report", ""],
            "--report must not be empty",
        ),
    ],
)
def test_explicit_empty_values_are_usage_errors_before_any_opener(
    argv: list[str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class UnexpectedService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("empty input must be rejected before an opener")

    monkeypatch.setattr("session_weaver.cli.ConceptService", UnexpectedService, raising=False)

    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == 2
    assert message in capsys.readouterr().err


def test_winddown_field_validation_is_structured_exit_two_with_no_schema_write(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _write(tmp_path / "invalid.json", b'{"concepts":[],"extra":true}')

    assert (
        main(
            [
                "winddown",
                "--session",
                "fixture-session-1",
                "--from",
                str(request),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 2
    )
    payload = _json_output(capsys, error=True)

    assert payload["writes"] == 0
    assert payload["errors"][0]["code"] == "extra_field"
    assert (
        production_store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='context_concepts'"
        ).fetchone()
        is None
    )


def test_lifecycle_cli_maps_validation_success_and_terminal_states(
    production_store: ProductionStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    quote = "Lifecycle CLI evidence"
    _capture(production_store, quote, key="lifecycle-cli")
    created = ConceptService(production_store.db_path, now=lambda: _NOW).winddown(
        "fixture-session-1",
        _winddown_document(quote),
        actor="fixture-model",
    )
    concept_id = created.concept_ids[0]
    db_args = ["--db", str(production_store.db_path)]

    assert main(["concept", "accept", concept_id, "--reason", "reviewed", *db_args]) == 0
    accepted = _json_output(capsys)
    assert accepted["standing"] == "accepted"
    assert accepted["writes"] == 1

    assert main(["concept", "accept", concept_id, "--reason", "again", *db_args]) == 2
    invalid = _json_output(capsys, error=True)
    assert invalid["writes"] == 0
    assert invalid["errors"][0]["code"] == "invalid_transition"

    assert main(["concept", "retire", concept_id, "--reason", "obsolete", *db_args]) == 0
    assert _json_output(capsys)["standing"] == "retired"

    assert main(["concept", "retire", concept_id, "--reason", "again", *db_args]) == 2
    terminal = _json_output(capsys, error=True)
    assert terminal["errors"][0]["code"] == "retired_terminal"


def test_legacy_accept_requires_bind_and_bind_file_uses_safe_service_path(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    statement = "CLI exact legacy binding statement"
    _capture(production_store, statement, key="legacy-cli-bind")
    legacy_id = _seed_legacy(production_store, statement=statement)
    db_args = ["--db", str(production_store.db_path)]

    assert main(["concept", "accept", legacy_id, "--reason", "historical label", *db_args]) == 2
    refused = _json_output(capsys, error=True)
    assert refused["writes"] == 0
    assert refused["errors"][0]["code"] == "legacy_unbound_requires_bind"

    binding = _write(
        tmp_path / "binding.json",
        json.dumps({"quotes": [{"quote": statement}]}).encode(),
    )
    assert (
        main(
            [
                "concept",
                "bind",
                legacy_id,
                "--from",
                str(binding),
                "--reason",
                "exact body found",
                *db_args,
            ]
        )
        == 0
    )
    bound = _json_output(capsys)
    assert bound["writes"] == 4
    assert bound["legacy_concept_id"] == legacy_id
    assert bound["concept_id"] == bound["assertion_id"]


@pytest.mark.parametrize("report_option", (None, "-", "file"))
def test_import_cli_dry_run_write_report_and_idempotence_are_content_free(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    report_option: str | None,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    private_marker = "PRIVATE-SYNTHETIC-BODY-MUST-NOT-APPEAR"
    _write(
        root / "concept.md",
        _okf_bytes(title="Private synthetic title", body=private_marker),
    )
    base = [
        "concept",
        "import-okf",
        str(root),
        "--db",
        str(production_store.db_path),
    ]
    if report_option is not None:
        report_target = "-" if report_option == "-" else str(tmp_path / "report.json")
        base.extend(["--report", report_target])

    assert main([*base, "--dry-run"]) == 0
    dry_run = _json_output(capsys)
    assert dry_run["scanned"] == 1
    assert dry_run["imported"] == 0
    assert dry_run["writes"] == 0
    assert private_marker not in json.dumps(dry_run)
    assert (
        production_store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='context_concepts'"
        ).fetchone()
        is None
    )
    if report_option == "file":
        assert json.loads((tmp_path / "report.json").read_text()) == dry_run

    assert main(base) == 0
    written = _json_output(capsys)
    assert written["imported"] == 1
    assert written["legacy_unbound"] == 1
    assert written["writes"] == 1
    assert private_marker not in json.dumps(written)
    if report_option == "file":
        assert json.loads((tmp_path / "report.json").read_text()) == written
        assert list(tmp_path.glob(".session-weaver-*.tmp")) == []

    assert main(base) == 0
    repeated = _json_output(capsys)
    assert repeated["already_present"] == 1
    assert repeated["imported"] == 0
    assert repeated["writes"] == 0


@pytest.mark.parametrize("unsafe_target", ("symlink", "directory", "symlink-parent"))
def test_import_report_refuses_unsafe_target_before_service(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unsafe_target: str,
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    _write(root / "concept.md", _okf_bytes())
    sentinel = _write(tmp_path / "sentinel.json", b"user-owned")
    if unsafe_target == "symlink":
        report = tmp_path / "report.json"
        report.symlink_to(sentinel)
    elif unsafe_target == "directory":
        report = tmp_path / "report-directory"
        report.mkdir()
    else:
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        report = linked_parent / "report.json"

    class UnexpectedService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("unsafe report target must be rejected before service")

    monkeypatch.setattr("session_weaver.cli.ConceptService", UnexpectedService, raising=False)

    assert (
        main(
            [
                "concept",
                "import-okf",
                str(root),
                "--report",
                str(report),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 2
    )
    payload = _json_output(capsys, error=True)
    assert payload["writes"] == 0
    assert payload["errors"][0]["code"] == "unsafe_report_target"
    assert sentinel.read_bytes() == b"user-owned"


@pytest.mark.parametrize("bad_directory", ("missing", "file", "symlink"))
def test_import_rejects_invalid_directory_before_service(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bad_directory: str,
) -> None:
    if bad_directory == "missing":
        root = tmp_path / "missing"
    elif bad_directory == "file":
        root = _write(tmp_path / "file", b"not a directory")
    else:
        target = tmp_path / "target"
        target.mkdir()
        root = tmp_path / "linked"
        root.symlink_to(target, target_is_directory=True)

    class UnexpectedService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("invalid directory must be rejected before service")

    monkeypatch.setattr("session_weaver.cli.ConceptService", UnexpectedService, raising=False)

    assert (
        main(
            [
                "concept",
                "import-okf",
                str(root),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 2
    )
    payload = _json_output(capsys, error=True)
    assert payload["errors"][0]["code"] == "unsafe_directory"
    assert payload["writes"] == 0


@pytest.mark.parametrize("operation", ("winddown", "import"))
def test_storage_runtime_failure_is_exit_one_and_sanitized(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    private_marker = "PRIVATE-STORAGE-FAILURE-DETAIL"
    if operation == "winddown":
        request = _write(
            tmp_path / "request.json",
            json.dumps(_winddown_document("unused quote")).encode(),
        )
        argv = [
            "winddown",
            "--session",
            "fixture-session-1",
            "--from",
            str(request),
            "--db",
            str(production_store.db_path),
        ]
    else:
        root = tmp_path / "okf"
        root.mkdir()
        _write(root / "concept.md", _okf_bytes())
        argv = ["concept", "import-okf", str(root), "--db", str(production_store.db_path)]

    class FailingService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError(private_marker)

    monkeypatch.setattr("session_weaver.cli.ConceptService", FailingService, raising=False)

    assert main(argv) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["writes"] == 0
    assert payload["error"] == "operation failed"
    assert private_marker not in captured.err
    assert str(production_store.db_path) not in captured.err


def test_owned_database_connections_close_after_cli_success_and_failure(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    quote = "Connection closure exact evidence"
    _capture(production_store, quote, key="closure-evidence")
    request = _write(
        tmp_path / "request.json",
        json.dumps(_winddown_document(quote)).encode(),
    )
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracked_connect(
        database: Any,
        *args: Any,
        **kwargs: Any,
    ) -> sqlite3.Connection:
        conn = real_connect(database, *args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)

    assert (
        main(
            [
                "winddown",
                "--session",
                "fixture-session-1",
                "--from",
                str(request),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert opened
    for conn in opened:
        _assert_closed(conn)


def test_atomic_report_setup_failure_closes_descriptor_and_removes_temporary_file(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "okf"
    root.mkdir()
    _write(root / "concept.md", _okf_bytes())
    report = tmp_path / "report.json"
    descriptors: list[int] = []

    def fail_permissions(descriptor: int, _mode: int) -> None:
        descriptors.append(descriptor)
        raise OSError("synthetic private chmod failure")

    monkeypatch.setattr("session_weaver.cli.os.fchmod", fail_permissions)

    assert (
        main(
            [
                "concept",
                "import-okf",
                str(root),
                "--report",
                str(report),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert json.loads(captured.err)["error"] == "operation failed"
    assert "synthetic private chmod failure" not in captured.err
    assert len(descriptors) == 1
    try:
        with pytest.raises(OSError):
            os.fstat(descriptors[0])
    finally:
        with suppress(OSError):
            os.close(descriptors[0])
    assert list(tmp_path.glob(".session-weaver-*.tmp")) == []
    assert not report.exists()


@pytest.mark.parametrize("operation", ("winddown", "import"))
def test_unavailable_project_is_structured_resolution_exit_two(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    if operation == "winddown":
        request = _write(
            tmp_path / "request.json",
            json.dumps(_winddown_document("project scope quote")).encode(),
        )
        argv = [
            "winddown",
            "--session",
            "fixture-session-1",
            "--from",
            str(request),
            "--project",
            "unavailable-project",
            "--db",
            str(production_store.db_path),
        ]
    else:
        root = tmp_path / "okf"
        root.mkdir()
        _write(root / "concept.md", _okf_bytes())
        argv = [
            "concept",
            "import-okf",
            str(root),
            "--project",
            "unavailable-project",
            "--db",
            str(production_store.db_path),
        ]

    assert main(argv) == 2
    payload = _json_output(capsys, error=True)
    assert payload["writes"] == 0
    assert payload["errors"][0]["code"] == "project_unavailable"
    assert (
        production_store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='context_concepts'"
        ).fetchone()
        is None
    )


def _assert_zero_import_counters(payload: dict[str, Any]) -> None:
    counter_names = {
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
    assert all(payload[name] == 0 for name in counter_names)


@pytest.mark.parametrize(
    ("option", "field"),
    [("--actor", "/actor"), ("--project", "/project")],
)
def test_import_overlong_call_fields_are_exit_two_with_zero_reconciled_counters(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    field: str,
) -> None:
    root = tmp_path / f"overlong-{option.removeprefix('--')}"
    root.mkdir()
    _write(root / "concept.md", _okf_bytes())

    assert (
        main(
            [
                "concept",
                "import-okf",
                str(root),
                option,
                "x" * 129,
                "--db",
                str(production_store.db_path),
            ]
        )
        == 2
    )
    payload = _json_output(capsys, error=True)

    _assert_zero_import_counters(payload)
    assert payload["errors"] == [{"path": "", "code": "too_long", "field": field}]
    assert (
        production_store.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='context_concepts'"
        ).fetchone()
        is None
    )


def test_import_unavailable_project_is_exit_two_with_zero_reconciled_counters(
    production_store: ProductionStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "unavailable-project-zero-counters"
    root.mkdir()
    _write(root / "concept.md", _okf_bytes())

    assert (
        main(
            [
                "concept",
                "import-okf",
                str(root),
                "--project",
                "unavailable-project",
                "--db",
                str(production_store.db_path),
            ]
        )
        == 2
    )
    payload = _json_output(capsys, error=True)

    _assert_zero_import_counters(payload)
    assert payload["errors"] == [{"path": "", "code": "project_unavailable", "field": "/project"}]


def test_report_intermediate_directory_swap_stays_on_pinned_descriptor(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import session_weaver.cli as cli_module

    root = tmp_path / "report-swap-okf"
    root.mkdir()
    _write(root / "concept.md", _okf_bytes())
    report_parent = tmp_path / "report-parent"
    report_parent.mkdir()
    pinned_parent = tmp_path / "pinned-report-parent"
    outside = tmp_path / "outside-report-parent"
    outside.mkdir()
    report = report_parent / "report.json"
    real_write = cli_module._write_report_atomic

    def swap_parent_then_write(target: object, payload: dict[str, Any]) -> None:
        report_parent.rename(pinned_parent)
        report_parent.symlink_to(outside, target_is_directory=True)
        real_write(target, payload)  # type: ignore[arg-type]

    monkeypatch.setattr(cli_module, "_write_report_atomic", swap_parent_then_write)

    assert (
        main(
            [
                "concept",
                "import-okf",
                str(root),
                "--report",
                str(report),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 0
    )
    payload = _json_output(capsys)

    assert json.loads((pinned_parent / "report.json").read_text()) == payload
    assert not (outside / "report.json").exists()
    assert list(pinned_parent.glob(".session-weaver-*.tmp")) == []
    assert list(outside.glob(".session-weaver-*.tmp")) == []


def test_post_commit_report_failure_emits_truthful_partial_success_and_keeps_rows(
    production_store: ProductionStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "post-commit-report-failure"
    root.mkdir()
    payload_bytes = _okf_bytes(title="Durable despite report failure")
    _write(root / "concept.md", payload_bytes)
    report = tmp_path / "failed-report.json"

    def fail_permissions(_descriptor: int, _mode: int) -> None:
        raise OSError("PRIVATE-POST-COMMIT-REPORT-FAILURE")

    monkeypatch.setattr("session_weaver.cli.os.fchmod", fail_permissions)

    assert (
        main(
            [
                "concept",
                "import-okf",
                str(root),
                "--report",
                str(report),
                "--db",
                str(production_store.db_path),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)

    assert payload["committed"] is True
    assert payload["report_error"] == "report_delivery_failed"
    assert payload["error"] == "operation failed"
    assert payload["parsed"] == 1
    assert payload["imported"] == 1
    assert payload["writes"] == 1
    assert payload["write_failures"] == 0
    assert "PRIVATE-POST-COMMIT-REPORT-FAILURE" not in captured.err
    expected_id = "legacy:" + __import__("hashlib").sha256(payload_bytes).hexdigest()
    assert production_store.conn.execute(
        "SELECT id FROM context_concepts WHERE id=?",
        (expected_id,),
    ).fetchone() == (expected_id,)
    assert not report.exists()
    assert list(tmp_path.glob(".session-weaver-*.tmp")) == []
