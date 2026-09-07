"""Clean empty-content message rows from a sessions.db, with a preservation packet.

The delete criterion is EXACTLY sync.py's anti-resurrection filter
(trim(coalesce(content,'')) = ''), so every removed row is a row the pinned
session-sync would refuse to re-insert from a peer. Sessions left with zero
messages afterwards are removed too (noise skeletons) and listed in the packet.

Safety: refuses the live DB unless --live is passed AND a packet is written.
Always run against a rehearsal copy first. Two-pass rule: a second run must
report 0 deletions.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    parser.add_argument("--packet-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="required to touch the live sessions.db")
    args = parser.parse_args()

    live = (Path.home() / ".config/studyloop/sessions.db").resolve()
    if args.db.resolve() == live and not args.live:
        raise SystemExit("Refusing the live sessions.db without --live")

    args.packet_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    target = "trim(coalesce(content,'')) = ''"

    # 1. Preservation packet: every row to be removed, verbatim.
    rows = [dict(r) for r in cur.execute(f"SELECT * FROM messages WHERE {target}")]
    packet = args.packet_dir / f"removed-messages-{stamp}.jsonl.gz"
    with gzip.open(packet, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 2. Delete messages, then orphaned (now-empty) sessions.
    cur.execute(f"DELETE FROM messages WHERE {target}")
    deleted_messages = cur.rowcount
    doomed_sessions = [
        dict(r)
        for r in cur.execute(
            "SELECT * FROM sessions s WHERE NOT EXISTS"
            " (SELECT 1 FROM messages m WHERE m.session_id = s.id)"
        )
    ]
    spacket = args.packet_dir / f"removed-sessions-{stamp}.jsonl.gz"
    with gzip.open(spacket, "wt", encoding="utf-8") as fh:
        for r in doomed_sessions:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    cur.execute(
        "DELETE FROM sessions WHERE NOT EXISTS"
        " (SELECT 1 FROM messages m WHERE m.session_id = sessions.id)"
    )
    deleted_sessions = cur.rowcount
    conn.commit()

    # 3. FTS rebuild + integrity.
    fts_note = "no messages_fts table"
    has_fts = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE name='messages_fts'"
    ).fetchone()
    if has_fts:
        cur.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
        fts_note = "rebuilt"
    quick = cur.execute("PRAGMA quick_check").fetchone()[0]
    fk = cur.execute("PRAGMA foreign_key_check").fetchall()
    remaining = cur.execute(
        f"SELECT count(*) FROM messages WHERE {target}"
    ).fetchone()[0]
    totals = cur.execute(
        "SELECT (SELECT count(*) FROM sessions), (SELECT count(*) FROM messages)"
    ).fetchone()
    conn.close()

    sums = args.packet_dir / f"SHA256SUMS-{stamp}"
    with open(sums, "w", encoding="utf-8") as fh:
        for p in (packet, spacket):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            fh.write(f"{digest}  {p.name}\n")

    print(
        json.dumps(
            {
                "db": str(args.db),
                "deleted_messages": deleted_messages,
                "deleted_sessions": deleted_sessions,
                "remaining_empty": remaining,
                "sessions_total": totals[0],
                "messages_total": totals[1],
                "quick_check": quick,
                "fk_violations": len(fk),
                "fts": fts_note,
                "packet": str(packet),
                "sessions_packet": str(spacket),
                "sums": str(sums),
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
