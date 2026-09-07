"""Build a REAL ontology (T-Box + A-Box) inside sessions.db, deterministically.

T-Box (schema):
  ontology_class(name, parent, description)          — class taxonomy (is-a)
  ontology_property(name, domain, range, description) — typed relations
A-Box (instances):
  ontology_individual(id, class, label, attrs)        — typed individuals w/ attributes
  ontology_relation(subject, predicate, object)       — triples, FK-checked against T-Box

Classes: Project, Harness, Session, SubagentSession⊂Session, Artifact, Command, TestRun
Properties: ranIn, conductedBy, childOf, touched, executed, produced

Everything is derived from existing rows (sessions, messages, ontology_structural)
with zero LLM involvement. Idempotent: rebuilds from scratch each run.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

DB = Path.home() / ".config/studyloop/sessions.db"
VERSION = "onto-v1-2026-09-06"

TBOX_CLASSES = [
    ("Project",         None,      "A codebase/topic identified by its filesystem root"),
    ("Harness",         None,      "A coding-agent tool that conducts sessions (claude_code, codex, ...)"),
    ("Session",         None,      "One recorded conversation between the user and an agent"),
    ("SubagentSession", "Session", "A session spawned by another session (sidechain/subagent)"),
    ("Artifact",        None,      "A file touched or referenced during work"),
    ("Command",         None,      "A shell command class, keyed by its binary"),
    ("TestRun",         None,      "A recorded test-suite execution with its verbatim summary"),
]
TBOX_PROPS = [
    ("ranIn",       "Session",         "Project", "The project a session worked in"),
    ("conductedBy", "Session",         "Harness", "The harness that produced the session"),
    ("childOf",     "SubagentSession", "Session", "Parent session that spawned this subagent"),
    ("touched",     "Session",         "Artifact", "Session referenced/edited this file"),
    ("executed",    "Session",         "Command",  "Session ran this command"),
    ("produced",    "Session",         "TestRun",  "Session produced this test result"),
]

RE_TEST_NUMS = re.compile(r"(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) deselected)?")


def main() -> None:
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.executescript("""
      DROP TABLE IF EXISTS ontology_relation;
      DROP TABLE IF EXISTS ontology_individual;
      DROP TABLE IF EXISTS ontology_property;
      DROP TABLE IF EXISTS ontology_class;
      CREATE TABLE ontology_class(name TEXT PRIMARY KEY, parent TEXT REFERENCES ontology_class(name), description TEXT);
      CREATE TABLE ontology_property(name TEXT PRIMARY KEY,
        domain TEXT NOT NULL REFERENCES ontology_class(name),
        range  TEXT NOT NULL REFERENCES ontology_class(name), description TEXT);
      CREATE TABLE ontology_individual(id TEXT PRIMARY KEY,
        class TEXT NOT NULL REFERENCES ontology_class(name), label TEXT NOT NULL, attrs JSON);
      CREATE TABLE ontology_relation(
        subject TEXT NOT NULL REFERENCES ontology_individual(id),
        predicate TEXT NOT NULL REFERENCES ontology_property(name),
        object TEXT NOT NULL REFERENCES ontology_individual(id),
        PRIMARY KEY(subject, predicate, object)) WITHOUT ROWID;
    """)
    conn.executemany("INSERT INTO ontology_class VALUES (?,?,?)", TBOX_CLASSES)
    conn.executemany("INSERT INTO ontology_property VALUES (?,?,?,?)", TBOX_PROPS)

    ind: dict[str, tuple] = {}
    rel: set[tuple] = set()

    def individual(iid, cls, label, attrs=None):
        if iid not in ind:
            ind[iid] = (iid, cls, label[:200], json.dumps(attrs or {}))
        return iid

    # Harness + Project + Session individuals, ranIn/conductedBy/childOf
    sessions = conn.execute(
        "SELECT id, source, project_path, git_branch, created_at, updated_at, metadata"
        " FROM sessions").fetchall()
    msg_counts = dict(conn.execute(
        "SELECT session_id, count(*) FROM messages GROUP BY session_id"))
    known_ids = {s[0] for s in sessions}
    for sid, source, ppath, branch, created, updated, meta in sessions:
        h = individual(f"harness:{source}", "Harness", source)
        p = individual(f"project:{ppath or 'unknown'}", "Project",
                       (ppath or "unknown").replace("/Users/ataylor/", "~/"),
                       {"path": ppath})
        parent = None
        if meta:
            m = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{27})/subagents/", meta)
            if m and m.group(1) in known_ids:
                parent = m.group(1)
        cls = "SubagentSession" if (sid.startswith("agent-") or parent) else "Session"
        s = individual(f"session:{sid}", cls, sid[:24],
                       {"created": created, "updated": updated, "branch": branch,
                        "messages": msg_counts.get(sid, 0)})
        rel.add((s, "ranIn", p))
        rel.add((s, "conductedBy", h))
        if parent:
            rel.add((s, "childOf", f"session:{parent}"))

    # Artifact / Command / TestRun individuals + relations, from tier-1 extraction
    for sid, etype, key, value in conn.execute(
            "SELECT session_id, type, key, value FROM ontology_structural"
            " WHERE type IN ('artifact','command','testrun')"):
        s = f"session:{sid}"
        if s not in ind:
            continue
        if etype == "artifact":
            o = individual(f"artifact:{value}", "Artifact",
                           value.replace("/Users/ataylor/", "~/"),
                           {"path": value, "ext": value.rsplit(".", 1)[-1]})
            rel.add((s, "touched", o))
        elif etype == "command":
            o = individual(f"command:{key}", "Command", key, {"binary": key})
            rel.add((s, "executed", o))
        else:
            m = RE_TEST_NUMS.search(value)
            attrs = {"summary": value}
            if m:
                attrs.update(passed=int(m.group(1)),
                             skipped=int(m.group(2) or 0),
                             deselected=int(m.group(3) or 0))
            o = individual(f"testrun:{sid}:{value[:60]}", "TestRun", value[:60], attrs)
            rel.add((s, "produced", o))

    conn.executemany("INSERT INTO ontology_individual VALUES (?,?,?,?)", ind.values())
    conn.executemany("INSERT OR IGNORE INTO ontology_relation VALUES (?,?,?)", rel)
    conn.commit()
    print(f"T-Box: {len(TBOX_CLASSES)} classes, {len(TBOX_PROPS)} properties")
    for cls, n in conn.execute(
            "SELECT class, count(*) FROM ontology_individual GROUP BY class ORDER BY 2 DESC"):
        print(f"  individuals {cls:<16} {n:>6}")
    for pred, n in conn.execute(
            "SELECT predicate, count(*) FROM ontology_relation GROUP BY predicate ORDER BY 2 DESC"):
        print(f"  relations   {pred:<16} {n:>6}")
    bad = conn.execute("""SELECT count(*) FROM ontology_relation r
        JOIN ontology_individual s ON s.id=r.subject
        JOIN ontology_individual o ON o.id=r.object
        JOIN ontology_property p ON p.name=r.predicate
        JOIN ontology_class sc ON sc.name=s.class
        WHERE NOT (s.class=p.domain OR sc.parent=p.domain)
           OR NOT (o.class=p.range OR (SELECT parent FROM ontology_class WHERE name=o.class)=p.range)
        """).fetchone()[0]
    print(f"  domain/range violations: {bad}")
    print(f"built in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
