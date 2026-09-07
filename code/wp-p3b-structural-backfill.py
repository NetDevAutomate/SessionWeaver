"""D v1 Tier-1 prototype — deterministic structural ontology backfill.

Populates contracted structural entities from the EXISTING clean DB (no native
files needed, no LLM, no cost): proof that 'ontology day 1' does not require a
from-scratch re-export. Writes to corpus-derived.db (PoC sidecar):

  ontology_structural(id, session_id, type, key, value, ts, extraction_version)

Types in this prototype:
  project   — project_path + git_branch per session (sessions table, 100% coverage)
  testrun   — pytest summary lines found verbatim in message text
  artifact  — absolute file paths mentioned in conversation text (deduped/session)
  command   — shell commands in fenced blocks or $-prefixed lines (deduped/session)
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path.home() / ".local/share/sessionweaver/poc-storage-decision"
VERSION = "tier1-proto-2026-09-06"

RE_TESTRUN = re.compile(r"(\d+ passed(?:, \d+ (?:skipped|xfailed|failed|deselected|xpassed))*[^\n]{0,40}in [\d.]+s)")
RE_PATH = re.compile(r"(?<![\w.])(/Users/[a-z]+/[\w./~-]{8,140}\.[A-Za-z0-9]{1,8})(?![\w/])")
RE_CMD = re.compile(r"(?:^\$ |^```(?:bash|zsh|sh)\n)([a-z][\w.-]+(?: [^\n`]{2,120})?)", re.M)


def main() -> None:
    t0 = time.time()
    src = sqlite3.connect(f"file:{ROOT / 'corpus-20260906-clean.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(ROOT / "corpus-derived.db")
    dst.executescript(
        """CREATE TABLE IF NOT EXISTS ontology_structural(
             id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
             key TEXT NOT NULL, value TEXT NOT NULL, ts TEXT,
             extraction_version TEXT NOT NULL);
           DELETE FROM ontology_structural;""")

    rows = []

    def add(sid, etype, key, value, ts=None):
        rows.append((str(uuid.uuid4()), sid, etype, key[:200], value[:500], ts, VERSION))

    # project entities — deterministic, 100% coverage
    for sid, path, branch, updated in src.execute(
            "SELECT id, project_path, git_branch, updated_at FROM sessions"):
        add(sid, "project", path or "unknown", branch or "", updated)

    # text-derived entities from the retrieval view
    view = dst.execute(
        "SELECT session_id, ts, text FROM messages_derived"
        " WHERE kind='conversation' AND dup=0")
    seen: dict[tuple, None] = {}
    for sid, ts, text in view:
        for m in RE_TESTRUN.finditer(text):
            k = (sid, "testrun", m.group(1))
            if k not in seen:
                seen[k] = None
                add(sid, "testrun", "pytest", m.group(1), ts)
        for m in RE_PATH.finditer(text):
            k = (sid, "artifact", m.group(1))
            if k not in seen:
                seen[k] = None
                add(sid, "artifact", "path", m.group(1), ts)
        for m in RE_CMD.finditer(text):
            cmd = m.group(1).strip()
            k = (sid, "command", cmd.split()[0])
            if k not in seen:
                seen[k] = None
                add(sid, "command", cmd.split()[0], cmd, ts)

    dst.executemany("INSERT INTO ontology_structural VALUES (?,?,?,?,?,?,?)", rows)
    dst.commit()
    for etype, cnt, sess_cnt in dst.execute(
            "SELECT type, count(*), count(DISTINCT session_id) FROM"
            " ontology_structural GROUP BY type ORDER BY 2 DESC"):
        print(f"{etype:<9} entities={cnt:>7}  sessions={sess_cnt:>5}")
    print(f"total {len(rows)} entities in {time.time()-t0:.1f}s, cost $0")


if __name__ == "__main__":
    main()
