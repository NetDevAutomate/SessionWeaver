"""WP-P0 — normalisation sidecar for the storage PoC.

Reads the frozen CLEAN corpus (read-only) and writes corpus-derived.db:
  messages_derived(id, session_id, source, role, ts, kind, dup, chars, text)
  sessions_derived(id, source, title, started_at, updated_at, n_conv)

kind: 'conversation' (user/assistant turns), 'event' (toolResult/info/error/system),
      'log' (heuristic: litellm/bedrock proxy traffic and giant pasted blobs stay
      indexable but are typed so candidates can weight them).
dup:  1 for exact-duplicate content repeats within the same session (first kept 0).

Every candidate indexes the SAME view: kind='conversation' AND dup=0, so the
comparison measures engines, not cleaning choices.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

ROOT = Path.home() / ".local/share/sessionweaver/poc-storage-decision"
SRC = ROOT / "corpus-20260906-clean.db"
DST = ROOT / "corpus-derived.db"


def classify(role: str, source: str, text: str) -> str:
    if role in ("toolResult", "info", "error", "system"):
        return "event"
    if text.startswith("[tool:") and len(text) < 120:
        return "event"  # tool-invocation stubs recorded as conversation turns
    if source in ("litellm-proxy", "bedrock_proxy", "omp"):
        return "log"
    if len(text) > 20000:
        return "log"
    return "conversation"


def main() -> None:
    t0 = time.time()
    if DST.exists():
        DST.unlink()
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    dst = sqlite3.connect(DST)
    dst.executescript(
        """
        CREATE TABLE messages_derived(
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, source TEXT NOT NULL,
          role TEXT, ts TEXT, kind TEXT NOT NULL, dup INTEGER NOT NULL DEFAULT 0,
          chars INTEGER NOT NULL, text TEXT NOT NULL);
        CREATE TABLE sessions_derived(
          id TEXT PRIMARY KEY, source TEXT, title TEXT,
          started_at TEXT, updated_at TEXT, n_conv INTEGER);
        """
    )
    seen: dict[str, set[str]] = {}
    rows = src.execute(
        "SELECT m.id, m.session_id, s.source, m.role, m.timestamp, m.content"
        " FROM messages m JOIN sessions s ON s.id = m.session_id"
    )
    batch = []
    n = 0
    for mid, sid, source, role, ts, content in rows:
        text = (content or "").strip()
        if not text:
            continue  # clean DB should have none; belt and braces
        digest = hashlib.sha1(text.encode()).hexdigest()
        bucket = seen.setdefault(sid, set())
        dup = 1 if digest in bucket else 0
        bucket.add(digest)
        batch.append(
            (mid, sid, source, role, ts, classify(role or "", source, text), dup, len(text), text)
        )
        n += 1
        if len(batch) >= 5000:
            dst.executemany("INSERT INTO messages_derived VALUES (?,?,?,?,?,?,?,?,?)", batch)
            batch = []
    if batch:
        dst.executemany("INSERT INTO messages_derived VALUES (?,?,?,?,?,?,?,?,?)", batch)
    dst.execute("ATTACH DATABASE ? AS src", (str(SRC),))
    dst.execute(
        "INSERT INTO sessions_derived "
        "SELECT s.id, s.source, s.project_path, s.created_at, s.updated_at,"
        " (SELECT count(*) FROM messages_derived d WHERE d.session_id=s.id"
        "   AND d.kind='conversation' AND d.dup=0)"
        " FROM src.sessions s",
    )
    dst.commit()
    dst.execute("DETACH DATABASE src")
    stats = dst.execute(
        "SELECT kind, dup, count(*), sum(chars) FROM messages_derived GROUP BY kind, dup"
    ).fetchall()
    dst.close()
    src.close()
    print(f"rows written: {n} in {time.time()-t0:.1f}s")
    for kind, dup, cnt, chars in stats:
        print(f"  kind={kind:<13} dup={dup}  rows={cnt:>7}  chars={chars:>11,}")


if __name__ == "__main__":
    main()
