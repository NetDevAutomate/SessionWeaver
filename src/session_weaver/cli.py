"""`session-weaver` CLI: install the skill, inspect state, and maintain ontology."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

from . import __version__
from .harnesses import HARNESSES, parse_harness_selection
from .installer import install_skill, status, uninstall_skill
from .ontology import OntologyError, OntologyStatus, ontology_status, rebuild_ontology


def _harness_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--harness",
        default="all",
        help=f"comma-separated harness names or 'all' (known: {', '.join(sorted(HARNESSES))})",
    )


def _db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="path to sessions.db (default: ~/.config/studyloop/sessions.db)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session-weaver",
        description="Cross-harness session memory: installer, doctor, and ontology maintenance.",
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
    return parser


def _database_path(db_arg: str | None) -> Path:
    return Path(db_arg).expanduser() if db_arg else Path.home() / ".config/studyloop/sessions.db"


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _emit_json(payload: dict[str, Any], *, error: bool = False) -> None:
    print(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
    )


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
    except (OntologyError, sqlite3.Error):
        _emit_json(
            {
                "command": "ontology status",
                "error": "database unavailable",
                "healthy": False,
            },
            error=True,
        )
        return 1

    payload = asdict(result)
    payload["command"] = "ontology status"
    _emit_json(payload)
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
                except sqlite3.Error:
                    print("FAIL  ontology inspection failed")
                    failures += 1
                else:
                    failures += _print_doctor_ontology(ontology_result)
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
