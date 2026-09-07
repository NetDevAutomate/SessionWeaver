"""Tier-2 wind-down simulation → OKF v0.2 store, for the tested subset.

Simulates the wind-down step D v1 prescribes: a capable model reads the FULL
session conversation (the fix for D v0's truncation failure) and authors
semantic knowledge concepts directly as OKF markdown (the tier-2 authoring
surface). Output: an OKF v0.2 bundle at okf-store/.

Subset rule (blind to gold): updated_at >= 2026-08-01 AND >= 10 non-empty
messages (348 sessions, 11.9M chars, 22/25 benchmark questions answerable).

Per session, the model emits 0-8 concepts of types Decision | Finding |
Problem | Preference | Procedure with OKF frontmatter:
  type, title, description, tags, sources (session provenance),
  verified: machine-confirmed convention, actor producer/version.

Modes: digests | estimate-input | run
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path.home() / ".local/share/sessionweaver/poc-storage-decision"
DB = ROOT / "corpus-day1.db"
STORE = ROOT / "okf-store"
RUN = "20260906T2145Z-t2-winddown"
MODEL = "claude-sonnet-5"
ACTOR = "sessionweaver-winddown/0.1"
SESSION_CHAR_CAP = 60000  # full text for the vast majority; head+tail for outliers
WORKERS = 8

RULE = "s.updated_at>='2026-08-01'"

PROMPT = """You are performing the wind-down step for a coding-agent session: distil what this
session KNOWS into knowledge concepts another agent can rely on later.

Emit 0-8 concepts. Allowed types: Decision, Finding, Problem, Preference, Procedure.
Rules:
- Only what the transcript supports. No invention. Skip chit-chat and routine tool noise.
- Keep exact names, numbers, paths, commands, error strings VERBATIM in the description.
- description: 1-3 sentences, self-contained (readable without the session).
- title: <= 12 words, specific (never generic like "Critical finding").
- tags: 2-5 lowercase topic tags.
- confidence: 0.5-1.0 (how clearly the transcript supports it).

Respond with STRICT JSON only:
{{"concepts": [{{"type": "Decision", "title": "...", "description": "...", "tags": ["..."], "confidence": 0.9}}, ...]}}

SESSION {sid} (source: {source}, project: {project}, updated: {updated}):
{text}
"""


def read_key() -> str:
    for line in (Path.home() / ".config/litellm-proxy-docker/.env").read_text().splitlines():
        if line.startswith(("LITELLM_MASTER_KEY=", "LITELLM_API_KEY=")):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("no key")


def subset(conn):
    return conn.execute(f"""
      SELECT s.id, s.source, s.project_path, s.updated_at FROM sessions s
      WHERE {RULE} AND (SELECT count(*) FROM messages m WHERE m.session_id=s.id
                        AND trim(coalesce(m.content,''))!='') >= 10""").fetchall()


def session_text(conn, sid):
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? AND"
        " trim(coalesce(content,''))!='' AND length(content)<20000"
        " AND NOT (content LIKE '[tool:%' AND length(content)<120)"
        " ORDER BY timestamp", (sid,)).fetchall()
    parts = [f"[{r}] {re.sub(r'\\s+', ' ', t)}" for r, t in rows]
    full = "\n".join(parts)
    if len(full) > SESSION_CHAR_CAP:
        half = SESSION_CHAR_CAP // 2
        full = full[:half] + "\n[...middle omitted...]\n" + full[-half:]
    return full


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "concept"


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "estimate-input"
    # "retry" behaves like "run" below
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA query_only=1")
    sess = subset(conn)
    if mode == "retry":
        import json as _j
        failed = {_j.loads(l)["sid"] for l in open(ROOT / "t2-extract-log.round1.jsonl")}
        sess = [x for x in sess if x[0] in failed]
        print(f"retrying {len(sess)} failed sessions")

    if mode == "estimate-input":
        total = 0
        for sid, *_ in sess:
            total += len(session_text(conn, sid))
        print(f"sessions {len(sess)}  prompt chars {total:,}  (~{total//4:,} tokens)")
        (ROOT / "t2-sample-prompt.txt").write_text(
            PROMPT.format(sid=sess[0][0], source=sess[0][1], project=sess[0][2],
                          updated=sess[0][3], text=session_text(conn, sess[0][0])))
        print("sample prompt written for litellm-cost estimate")
        return

    assert (ROOT / "estimate-t2.json").exists(), "estimate first"  # covers retry too
    STORE.mkdir(exist_ok=True)
    key = read_key()
    log = (ROOT / "t2-extract-log.jsonl").open("a")

    def one(item):
        sid, source, project, updated = item
        text = session_text(conn2 := sqlite3.connect(DB), sid)
        conn2.close()
        prompt = PROMPT.format(sid=sid, source=source, project=project,
                               updated=updated, text=text)
        try:
            with httpx.Client(base_url="http://127.0.0.1:4000", timeout=240.0) as client:
                r = client.post("/v1/chat/completions", json={
                    "model": MODEL, "temperature": 0, "max_tokens": 4000,
                    "metadata": {"tags": [f"run:{RUN}"]},
                    "messages": [{"role": "user", "content": prompt}]},
                    headers={"Authorization": f"Bearer {key}",
                             "x-litellm-tags": f"run:{RUN}"})
                r.raise_for_status()
                out = r.json()["choices"][0]["message"]["content"].strip()
                out = re.sub(r"^```(json)?|```$", "", out, flags=re.M).strip()
                data = json.loads(out)
        except Exception as exc:  # noqa: BLE001
            return sid, None, str(exc)[:200]
        return sid, data.get("concepts", []), None

    n_files = 0
    failures = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sid, concepts, err in ex.map(one, sess):
            if err is not None:
                failures += 1
                log.write(json.dumps({"sid": sid, "error": err}) + "\n")
                continue
            sdir = STORE / "sessions" / sid[:2]
            sdir.mkdir(parents=True, exist_ok=True)
            for i, c in enumerate(concepts):
                if not isinstance(c, dict) or c.get("type") not in (
                        "Decision", "Finding", "Problem", "Preference", "Procedure"):
                    continue
                fname = sdir / f"{sid[:12]}-{i:02d}-{slugify(c.get('title',''))}.md"
                tags = [t for t in (c.get("tags") or []) if isinstance(t, str)][:5]
                fm = "\n".join([
                    "---",
                    f"type: {c['type']}",
                    f"title: {json.dumps(str(c.get('title',''))[:120])}",
                    f"description: {json.dumps(str(c.get('description',''))[:500])}",
                    f"tags: {json.dumps(tags)}",
                    "sources:",
                    f"  - resource: sessionweaver://session/{sid}",
                    f"    role: transcript",
                    f"verified:",
                    f"  status: machine-confirmed",
                    f"  by: {ACTOR}",
                    f"confidence: {float(c.get('confidence') or 0.7)}",
                    f"actor: {ACTOR}",
                    "---",
                    "",
                    str(c.get("description", "")),
                ])
                fname.write_text(fm)
                n_files += 1
    log.close()
    print(f"DONE: {n_files} OKF concepts, {failures} failed sessions,"
          f" {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
