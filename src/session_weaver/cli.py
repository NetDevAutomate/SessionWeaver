"""`session-weaver` CLI: install, inspect, and perform bounded database writes."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
from contextlib import closing, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, BinaryIO, TextIO
from uuid import uuid4

from agent_session_tools.context.scope import ScopeError

from . import __version__
from .concepts import BatchResult, BindResult, ConceptService, TransitionResult
from .harnesses import HARNESSES, parse_harness_selection
from .installer import install_skill, status, uninstall_skill
from .okf import ImportReport
from .ontology import OntologyError, OntologyStatus, ontology_status, rebuild_ontology
from .projection import ProjectionReport
from .recall import RecallReport, recall
from .safe_fs import _FILE_CREATE_FLAGS, _open_directory_nofollow
from .winddown import MAX_REQUEST_BYTES, _Issue

_DEFAULT_WINDDOWN_ACTOR = "session-weaver/winddown"
_DEFAULT_OPERATOR_ACTOR = "session-weaver/operator"
_DEFAULT_IMPORT_ACTOR = "session-weaver/import-okf"


class _InputFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class _ReportTarget:
    parent_descriptor: int
    name: str


def _harness_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--harness",
        default="all",
        help=f"comma-separated harness names or 'all' (known: {', '.join(sorted(HARNESSES))})",
    )


def _nonempty(value: str, label: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError(f"{label} must not be empty")
    return value


def _nonempty_type(label: str) -> Any:
    return lambda value: _nonempty(value, label)


def _nonempty_db_arg(value: str) -> str:
    return _nonempty(value, "--db")


def _k_arg(value: str) -> int:
    if not value.strip():
        raise argparse.ArgumentTypeError("--k must not be empty")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--k must be an integer between 1 and 50") from exc
    if not 1 <= parsed <= 50:
        raise argparse.ArgumentTypeError("--k must be an integer between 1 and 50")
    return parsed


def _db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        type=_nonempty_db_arg,
        help="path to sessions.db (default: ~/.config/studyloop/sessions.db)",
    )


def _concept_common_args(
    parser: argparse.ArgumentParser,
    *,
    default_actor: str,
    reason: bool = False,
) -> None:
    if reason:
        parser.add_argument("--reason", required=True, type=_nonempty_type("--reason"))
    parser.add_argument("--actor", default=default_actor, type=_nonempty_type("--actor"))
    parser.add_argument("--project", default=None, type=_nonempty_type("--project"))
    _db_arg(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session-weaver",
        description="Cross-harness session memory: installer, ontology, and concept maintenance.",
    )
    parser.add_argument("--version", action="version", version=f"session-weaver {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser(
        "install", help="install the session-weaver skill (hub + harness links)"
    )
    _harness_arg(p_install)
    p_install.add_argument(
        "--copy",
        action="store_true",
        help="copy into each selected non-hub-reading harness instead of symlinking",
    )
    p_install.add_argument(
        "--force",
        action="store_true",
        help="with --copy, replace only an existing named harness target",
    )
    p_install.add_argument("--dry-run", action="store_true", help="report without changing files")

    p_uninstall = sub.add_parser("uninstall", help="remove harness links/copies")
    _harness_arg(p_uninstall)
    p_uninstall.add_argument(
        "--remove-hub", action="store_true", help="also remove the canonical hub copy"
    )
    p_uninstall.add_argument("--dry-run", action="store_true")

    p_status = sub.add_parser("status", help="show the installation state per harness")
    _harness_arg(p_status)

    p_doctor = sub.add_parser("doctor", help="check the session store, ontology, and tools")
    _db_arg(p_doctor)

    p_ontology = sub.add_parser("ontology", help="rebuild or inspect the Tier-1 ontology")
    ontology_sub = p_ontology.add_subparsers(dest="ontology_command", required=True)
    p_rebuild = ontology_sub.add_parser("rebuild", help="atomically rebuild the ontology")
    p_rebuild.add_argument(
        "--incremental",
        action="store_true",
        help="reuse unchanged extraction rows when the previous state is valid",
    )
    _db_arg(p_rebuild)
    p_ontology_status = ontology_sub.add_parser(
        "status", help="inspect ontology health without mutating the database"
    )
    _db_arg(p_ontology_status)

    p_winddown = sub.add_parser("winddown", help="write a bounded evidence-backed wind-down")
    p_winddown.add_argument("--session", required=True, type=_nonempty_type("--session"))
    winddown_input = p_winddown.add_mutually_exclusive_group(required=True)
    winddown_input.add_argument("--from", dest="input_file", type=_nonempty_type("--from"))
    winddown_input.add_argument("--stdin", action="store_true")
    _concept_common_args(p_winddown, default_actor=_DEFAULT_WINDDOWN_ACTOR)

    p_recall = sub.add_parser(
        "recall", help="concept-first AND->OR recall over concepts then sessions"
    )
    p_recall.add_argument("question", type=_nonempty_type("question"))
    p_recall.add_argument("--k", default=5, type=_k_arg)
    p_recall.add_argument("--project", default=None, type=_nonempty_type("--project"))
    p_recall.add_argument("--json", action="store_true", help="emit deterministic JSON")
    _db_arg(p_recall)

    p_concept = sub.add_parser("concept", help="manage concept lifecycle and legacy imports")
    concept_sub = p_concept.add_subparsers(dest="concept_command", required=True)
    for command in ("accept", "retire"):
        lifecycle = concept_sub.add_parser(command, help=f"{command} one concept")
        lifecycle.add_argument("concept_id", type=_nonempty_type("concept id"))
        _concept_common_args(lifecycle, default_actor=_DEFAULT_OPERATOR_ACTOR, reason=True)

    p_bind = concept_sub.add_parser("bind", help="bind a legacy root to exact evidence")
    p_bind.add_argument("concept_id", type=_nonempty_type("concept id"))
    p_bind.add_argument("--from", dest="input_file", required=True, type=_nonempty_type("--from"))
    _concept_common_args(p_bind, default_actor=_DEFAULT_OPERATOR_ACTOR, reason=True)

    p_import = concept_sub.add_parser("import-okf", help="import a recursive legacy OKF tree")
    p_import.add_argument("directory", type=_nonempty_type("directory"))
    p_import.add_argument("--report", default=None, type=_nonempty_type("--report"))
    p_import.add_argument("--dry-run", action="store_true")
    _concept_common_args(p_import, default_actor=_DEFAULT_IMPORT_ACTOR)

    p_project = concept_sub.add_parser(
        "project", help="rebuild disposable scope-authorized Markdown"
    )
    p_project.add_argument("--out", required=True, type=_nonempty_type("--out"))
    p_project.add_argument("--project", default=None, type=_nonempty_type("--project"))
    p_project.add_argument("--json", action="store_true", help="emit deterministic JSON")
    _db_arg(p_project)
    return parser


def _database_path(db_arg: str | None) -> Path:
    if db_arg is None:
        return Path.home() / ".config/studyloop/sessions.db"
    return Path(db_arg).expanduser()


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _emit_json(payload: dict[str, Any], *, error: bool = False) -> None:
    print(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
    )


def _issue_payload(issue: _Issue) -> dict[str, str]:
    return {"path": issue.path, "code": issue.code, "message": issue.message}


def _input_error_payload(command: str, failure: _InputFailure) -> dict[str, Any]:
    return {
        "command": command,
        "errors": [{"path": "/", "code": failure.code, "message": failure.message}],
        "writes": 0,
    }


def _runtime_failure(command: str) -> int:
    _emit_json(
        {"command": command, "error": "operation failed", "writes": 0},
        error=True,
    )
    return 1


def _scope_failure(command: str, *, project: str | None) -> int:
    code = "project_unavailable" if project is not None else "scope_unavailable"
    path = "/project" if project is not None else "/scope"
    _emit_json(
        {
            "command": command,
            "errors": [
                {
                    "path": path,
                    "code": code,
                    "message": "Configured scope is unavailable",
                }
            ],
            "writes": 0,
        },
        error=True,
    )
    return 2


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_REQUEST_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(8192, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > MAX_REQUEST_BYTES:
        raise _InputFailure("input_too_large", "Input exceeds the bounded request limit")
    return payload


def _read_input_file(value: str) -> bytes:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise _InputFailure("unsafe_input", "Input must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _InputFailure("unsafe_input", "Input could not be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _InputFailure("unsafe_input", "Input must be a regular file")
        if metadata.st_size > MAX_REQUEST_BYTES:
            raise _InputFailure("input_too_large", "Input exceeds the bounded request limit")
        return _read_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _read_stdin() -> bytes:
    source: BinaryIO | TextIO = getattr(sys.stdin, "buffer", sys.stdin)
    value = source.read(MAX_REQUEST_BYTES + 1)
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise _InputFailure("input_too_large", "Input exceeds the bounded request limit")
    return payload


def _safe_import_directory(value: str) -> Path:
    root = Path(value).expanduser()
    try:
        descriptor = _open_directory_nofollow(root)
    except OSError as exc:
        raise _InputFailure(
            "unsafe_directory", "Directory must be a non-symlink directory"
        ) from exc
    os.close(descriptor)
    return root


def _safe_report_target(value: str | None) -> _ReportTarget | None:
    if value in (None, "-"):
        return None
    target = Path(value).expanduser()
    if target.name in ("", ".", ".."):
        raise _InputFailure("unsafe_report_target", "Report target is unsafe")
    try:
        parent_descriptor = _open_directory_nofollow(target.parent)
    except OSError as exc:
        raise _InputFailure("unsafe_report_target", "Report target is unsafe") from exc
    try:
        try:
            metadata = os.stat(
                target.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(metadata.st_mode):
                raise _InputFailure("unsafe_report_target", "Report target is unsafe")
        return _ReportTarget(parent_descriptor=parent_descriptor, name=target.name)
    except Exception:
        os.close(parent_descriptor)
        raise


def _write_report_atomic(target: _ReportTarget, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor: int | None = None
    temporary_name = ""
    for _ in range(16):
        temporary_name = f".session-weaver-{uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                _FILE_CREATE_FLAGS,
                0o600,
                dir_fd=target.parent_descriptor,
            )
            break
        except FileExistsError:
            continue
    if descriptor is None:
        raise OSError("Unable to allocate a private report temporary file")

    descriptor_open = True
    temporary_exists = True
    try:
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "wb")
        descriptor_open = False
        with stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            metadata = os.stat(
                target.name,
                dir_fd=target.parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(metadata.st_mode):
                raise _InputFailure("unsafe_report_target", "Report target became unsafe")
        os.replace(
            temporary_name,
            target.name,
            src_dir_fd=target.parent_descriptor,
            dst_dir_fd=target.parent_descriptor,
        )
        temporary_exists = False
        os.fsync(target.parent_descriptor)
    finally:
        if descriptor_open:
            os.close(descriptor)
        if temporary_exists:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=target.parent_descriptor)


def _batch_payload(result: BatchResult) -> dict[str, Any]:
    return {
        "command": "winddown",
        "writes": result.writes,
        "concept_ids": list(result.concept_ids),
        "errors": [_issue_payload(issue) for issue in result.errors],
    }


def _transition_payload(command: str, result: TransitionResult) -> dict[str, Any]:
    return {
        "command": command,
        "writes": result.writes,
        "concept_id": result.concept_id,
        "standing": result.standing,
        "event_id": result.event_id,
        "errors": [_issue_payload(issue) for issue in result.errors],
    }


def _bind_payload(result: BindResult) -> dict[str, Any]:
    return {
        "command": "concept bind",
        "writes": result.writes,
        "legacy_concept_id": result.legacy_concept_id,
        "concept_id": result.concept_id,
        "assertion_id": result.assertion_id,
        "errors": [_issue_payload(issue) for issue in result.errors],
    }


def _winddown(args: argparse.Namespace) -> int:
    try:
        document = _read_stdin() if args.stdin else _read_input_file(args.input_file)
    except _InputFailure as failure:
        _emit_json(_input_error_payload("winddown", failure), error=True)
        return 2
    try:
        service = ConceptService(_database_path(args.db), prepare_schema=False)
        result = service.winddown(
            args.session,
            document,
            actor=args.actor,
            project=args.project,
        )
    except ScopeError:
        return _scope_failure("winddown", project=args.project)
    except Exception:
        return _runtime_failure("winddown")
    payload = _batch_payload(result)
    _emit_json(payload, error=bool(result.errors))
    return 2 if result.errors else 0


def _concept_transition(args: argparse.Namespace) -> int:
    command = f"concept {args.concept_command}"
    try:
        service = ConceptService(_database_path(args.db), prepare_schema=False)
        result = service.transition(
            args.concept_id,
            "accepted" if args.concept_command == "accept" else "retired",
            actor=args.actor,
            reason=args.reason,
            project=args.project,
        )
    except ScopeError:
        return _scope_failure(command, project=args.project)
    except Exception:
        return _runtime_failure(command)
    payload = _transition_payload(command, result)
    _emit_json(payload, error=bool(result.errors))
    return 2 if result.errors else 0


def _concept_bind(args: argparse.Namespace) -> int:
    try:
        document = _read_input_file(args.input_file)
    except _InputFailure as failure:
        _emit_json(_input_error_payload("concept bind", failure), error=True)
        return 2
    try:
        service = ConceptService(_database_path(args.db), prepare_schema=False)
        result = service.bind_legacy(
            args.concept_id,
            document,
            actor=args.actor,
            reason=args.reason,
            project=args.project,
        )
    except ScopeError:
        return _scope_failure("concept bind", project=args.project)
    except Exception:
        return _runtime_failure("concept bind")
    payload = _bind_payload(result)
    _emit_json(payload, error=bool(result.errors))
    return 2 if result.errors else 0


def _concept_import(args: argparse.Namespace) -> int:
    report_target: _ReportTarget | None = None
    try:
        root = _safe_import_directory(args.directory)
        report_target = _safe_report_target(args.report)
    except _InputFailure as failure:
        _emit_json(_input_error_payload("concept import-okf", failure), error=True)
        return 2
    try:
        service = ConceptService(_database_path(args.db), prepare_schema=False)
        report: ImportReport = service.import_okf(
            root,
            actor=args.actor,
            project=args.project,
            dry_run=args.dry_run,
        )
        payload = report.to_dict()
        if report_target is not None:
            try:
                _write_report_atomic(report_target, payload)
            except Exception:
                operation_error = any(error.relative_path == "" for error in report.errors)
                partial = {
                    **payload,
                    "committed": bool(
                        not args.dry_run and not report.write_failures and not operation_error
                    ),
                    "error": "operation failed",
                    "report_error": "report_delivery_failed",
                }
                _emit_json(partial, error=True)
                return 1
    except _InputFailure as failure:
        _emit_json(_input_error_payload("concept import-okf", failure), error=True)
        return 2
    except Exception:
        return _runtime_failure("concept import-okf")
    finally:
        if report_target is not None:
            with suppress(OSError):
                os.close(report_target.parent_descriptor)
    if report.write_failures:
        _emit_json(payload, error=True)
        return 1
    if any(error.relative_path == "" for error in report.errors):
        _emit_json(payload, error=True)
        return 2
    _emit_json(payload)
    return 0


def _projection_payload(args: argparse.Namespace, report: ProjectionReport) -> dict[str, Any]:
    return {
        "command": "concept project",
        "out": args.out,
        **report.to_dict(),
    }


def _concept_project(args: argparse.Namespace) -> int:
    try:
        service = ConceptService(_database_path(args.db), prepare_schema=False)
        report = service.project(Path(args.out).expanduser(), project=args.project)
    except ScopeError:
        return _scope_failure("concept project", project=args.project)
    except Exception:
        return _runtime_failure("concept project")
    payload = _projection_payload(args, report)
    failed = report.status != "ok"
    if args.json:
        _emit_json(payload, error=failed)
    else:
        print(
            " ".join(
                f"{key}={json.dumps(value, ensure_ascii=False, sort_keys=True)}"
                for key, value in payload.items()
            ),
            file=sys.stderr if failed else sys.stdout,
        )
    return 1 if failed else 0


def _recall_payload(report: RecallReport) -> dict[str, Any]:
    return {"command": "recall", **report.to_dict()}


def _print_recall_text(report: RecallReport) -> None:
    print(f"concepts ({len(report.concepts)}):")
    for concept in report.concepts:
        print(
            f"  [{concept.kind}] {concept.title}"
            f" ({concept.provenance_label}, standing={concept.standing})"
        )
        print(f"    {concept.statement}")
    print(f"sessions ({len(report.sessions)}):")
    for session in report.sessions:
        print(f"  [{session.source}] {session.session_id} {session.project_path or ''}")
        print(f"    {session.preview}")


def _recall(args: argparse.Namespace) -> int:
    try:
        report = recall(_database_path(args.db), args.question, k=args.k, project=args.project)
    except Exception:
        return _runtime_failure("recall")
    if args.json:
        _emit_json(_recall_payload(report))
    else:
        _print_recall_text(report)
    return 0


def _ontology_rebuild(db_arg: str | None, *, incremental: bool) -> int:
    started = perf_counter()
    try:
        with closing(sqlite3.connect(_database_path(db_arg))) as conn:
            result = rebuild_ontology(conn, incremental=incremental)
    except (OntologyError, sqlite3.Error):
        _emit_json(
            {
                "command": "ontology rebuild",
                "error": "rebuild failed",
                "ok": False,
            },
            error=True,
        )
        return 1

    payload = asdict(result)
    payload.update(
        {
            "command": "ontology rebuild",
            "elapsed_seconds": round(perf_counter() - started, 6),
            "ok": True,
        }
    )
    _emit_json(payload)
    return 0


def _ontology_status(db_arg: str | None) -> int:
    try:
        with closing(sqlite3.connect(_read_only_uri(_database_path(db_arg)), uri=True)) as conn:
            conn.execute("PRAGMA query_only = ON")
            result = ontology_status(conn)
        payload = asdict(result)
        payload["command"] = "ontology status"
        _emit_json(payload)
    except Exception:
        _emit_json(
            {
                "command": "ontology status",
                "error": "database unavailable",
                "healthy": False,
            },
            error=True,
        )
        return 1

    return 0 if result.healthy else 1


def _print_doctor_ontology(result: OntologyStatus) -> int:
    if result.healthy:
        print(
            f"ok    ontology {result.extraction_version}: "
            f"coverage={result.coverage_ratio:.2%} "
            "fresh=ok hash=ok fk=0 domain-range=0"
        )
        return 0

    print("FAIL  ontology unhealthy")
    for diagnostic in result.diagnostics:
        print(f"      {diagnostic}")
    return 1


def _doctor(db_arg: str | None) -> int:
    import shutil as _shutil

    db = _database_path(db_arg)
    failures = 0
    if db.is_file():
        try:
            with closing(sqlite3.connect(_read_only_uri(db), uri=True)) as conn:
                conn.execute("PRAGMA query_only = ON")
                sessions, messages = (
                    conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                )
                print(f"ok    session store {db}: {sessions} sessions / {messages} messages")
                try:
                    ontology_result = ontology_status(conn)
                    failures += _print_doctor_ontology(ontology_result)
                except Exception:
                    print("FAIL  ontology inspection failed")
                    failures += 1
        except sqlite3.Error as exc:
            print(f"FAIL  session store {db}: unreadable ({exc})")
            failures += 1
    else:
        print(f"FAIL  session store missing: {db} (run session-export to create it)")
        failures += 1
    for tool in ("session-export", "session-query", "session-sync", "session-repair"):
        path = _shutil.which(tool)
        if path:
            print(f"ok    {tool} -> {path}")
        else:
            print(f"FAIL  {tool} not on PATH")
            failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor(args.db)
    if args.command == "ontology":
        if args.ontology_command == "rebuild":
            return _ontology_rebuild(args.db, incremental=args.incremental)
        return _ontology_status(args.db)
    if args.command == "winddown":
        return _winddown(args)
    if args.command == "recall":
        return _recall(args)
    if args.command == "concept":
        if args.concept_command in ("accept", "retire"):
            return _concept_transition(args)
        if args.concept_command == "bind":
            return _concept_bind(args)
        if args.concept_command == "project":
            return _concept_project(args)
        return _concept_import(args)

    try:
        harnesses = parse_harness_selection(args.harness)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "install":
        if args.force and not args.copy:
            print("error: --force requires --copy", file=sys.stderr)
            return 2
        report = install_skill(
            harnesses,
            mode="copy" if args.copy else "symlink",
            force=args.force,
            dry_run=args.dry_run,
        )
    elif args.command == "uninstall":
        report = uninstall_skill(harnesses, remove_hub=args.remove_hub, dry_run=args.dry_run)
    else:  # status
        report = status(harnesses)

    for action in report.actions:
        print(action)
    if report.conflicts:
        print(f"\n{len(report.conflicts)} conflict(s) need a human decision.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
