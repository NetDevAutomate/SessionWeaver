"""Benchmark candidate T2: the OKF wind-down store, on the same 25 gold questions.

T2 retrieval: FTS over OKF concept files (frontmatter title/description/tags + body),
hits mapped to sessions via the `sources` provenance line. Also measures T2+H fusion.
Coverage note: the OKF store covers the blind subset (updated>=2026-08-01, >=10 msgs);
22/25 questions have >=1 gold session inside it — the other 3 score what they score.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np

ROOT = Path.home() / ".local/share/sessionweaver/poc-storage-decision"
DB = ROOT / "corpus-day1.db"
STORE = ROOT / "okf-store"
STOP = set(
    "a an the is are was were be been being do does did to of in on for with"
    " and or not what which who why how when where whose that this these those"
    " it its during every any can cant can't could should would will shall"
    " about into from as at by we our your my i you they them he she his her".split())


def terms(q):
    return [t for t in re.findall(r"[a-zA-Z0-9_./-]+", q.lower())
            if t not in STOP and len(t) > 2]


def score(golds, ranked):
    hit = int(any(s in golds for s in ranked))
    mrr = next((1.0/(i+1) for i, s in enumerate(ranked) if s in golds), 0.0)
    return hit, mrr


def rrf(*lists, k=5, c=60):
    sc = {}
    for lst in lists:
        for i, s in enumerate(lst):
            sc[s] = sc.get(s, 0.0) + 1.0/(c+i+1)
    return [s for s, _ in sorted(sc.items(), key=lambda kv: -kv[1])][:k]


def main():
    gold = json.loads((ROOT / "gold.json").read_text())
    conn = sqlite3.connect(DB)

    # index the OKF store
    t0 = time.time()
    conn.executescript(
        "DROP TABLE IF EXISTS okf_fts;"
        "CREATE VIRTUAL TABLE okf_fts USING fts5(text, session_id UNINDEXED);")
    n = 0
    for f in STORE.rglob("*.md"):
        txt = f.read_text()
        m = re.search(r"sessionweaver://session/(\S+)", txt)
        if not m:
            continue
        conn.execute("INSERT INTO okf_fts(text, session_id) VALUES (?,?)",
                     (txt, m.group(1)))
        n += 1
    conn.commit()
    print(f"OKF index: {n} concepts in {time.time()-t0:.1f}s")

    def okf(question, k=5):
        ts = terms(question)
        out = []
        for query in (" AND ".join(f'"{t}"' for t in ts),
                      " OR ".join(f'"{t}"' for t in ts)):
            if not query:
                continue
            try:
                rows = conn.execute(
                    "SELECT session_id FROM okf_fts WHERE okf_fts MATCH ?"
                    " ORDER BY rank LIMIT 200", (query,)).fetchall()
            except sqlite3.OperationalError:
                continue
            for (s,) in rows:
                if s not in out:
                    out.append(s)
                    if len(out) == k:
                        return out
        return out

    # rebuild raw-text candidates (same as bench-day1)
    def planned(table, question, k=5):
        ts = terms(question)
        out = []
        for query in (" AND ".join(f'"{t}"' for t in ts),
                      " OR ".join(f'"{t}"' for t in ts)):
            if not query:
                continue
            try:
                rows = conn.execute(
                    f"SELECT session_id FROM {table} WHERE {table} MATCH ?"
                    " ORDER BY rank LIMIT 300", (query,)).fetchall()
            except sqlite3.OperationalError:
                continue
            for (s,) in rows:
                if s not in out:
                    out.append(s)
                    if len(out) == k:
                        return out
        return out

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    rows = conn.execute(
        "SELECT session_id FROM messages WHERE trim(coalesce(content,''))!=''"
        " AND length(content)<20000 AND NOT (content LIKE '[tool:%' AND length(content)<120)"
    ).fetchall()
    sess = np.array([r[0] for r in rows])
    emb = np.load(ROOT / "embeddings-day1.npy")

    def vec(question, k=5):
        qv = model.encode([question], normalize_embeddings=True).astype(np.float32)[0]
        order = np.argsort(-(emb @ qv))
        out = []
        for i in order:
            s = str(sess[i])
            if s not in out:
                out.append(s)
                if len(out) == k:
                    break
        return out

    results = []
    for q in gold:
        golds = set(q["gold"])
        a2 = planned("d1_fts", q["question"])
        b = vec(q["question"])
        t2 = okf(q["question"])
        h = rrf(a2, b)
        t2h = rrf(t2, a2, b)
        results.append({"id": q["id"], "type": q["type"],
                        "A2": score(golds, a2), "B": score(golds, b),
                        "T2": score(golds, t2), "H": score(golds, h),
                        "T2H": score(golds, t2h)})

    def agg(key, qtype=None):
        sel = [r for r in results if qtype is None or r["type"] == qtype]
        return (sum(r[key][0] for r in sel)/len(sel),
                sum(r[key][1] for r in sel)/len(sel))

    print("\n=== T2 (OKF wind-down store) vs raw-text — recall@5 / MRR@5 (n=25, higher better) ===")
    for c in ("A2", "B", "T2", "H", "T2H"):
        a = agg(c)
        print(f"{c:>4}: all {a[0]:.2f}/{a[1]:.2f} | K {agg(c,'K')[0]:.2f} |"
              f" P {agg(c,'P')[0]:.2f} | R {agg(c,'R')[0]:.2f}")
    (ROOT / "bench-results-t2.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
