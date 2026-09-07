"""`session-weaver` CLI: install/uninstall the agent skill, inspect state.

The session tools themselves (session-export, session-query, session-sync, …)
are separate console scripts provided by this same distribution — see
`pyproject.toml` [project.scripts].
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .harnesses import HARNESSES, parse_harness_selection
from .installer import install_skill, status, uninstall_skill


def _harness_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--harness",
        default="all",
        help=f"comma-separated harness names or 'all' (known: {', '.join(sorted(HARNESSES))})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="session-weaver",
        description="Cross-harness session memory: skill installer and environment doctor.",
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
        help="copy the skill into each harness directory instead of symlinking to the hub",
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

    p_doctor = sub.add_parser("doctor", help="check the session store and tools are reachable")
    p_doctor.add_argument(
        "--db",
        default=None,
        help="path to sessions.db (default: ~/.config/studyloop/sessions.db)",
    )
    return parser


def _doctor(db_arg: str | None) -> int:
    import shutil as _shutil
    import sqlite3
    from contextlib import closing

    db = Path(db_arg) if db_arg else Path.home() / ".config/studyloop/sessions.db"
    failures = 0
    if db.is_file():
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                sessions, messages = (
                    conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                )
            print(f"ok    session store {db}: {sessions} sessions / {messages} messages")
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

    try:
        harnesses = parse_harness_selection(args.harness)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "install":
        report = install_skill(
            harnesses,
            mode="copy" if args.copy else "symlink",
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
