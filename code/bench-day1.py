"""Benchmark on the DAY-1 DB structure (post-cutover, merged, with real ontology).

Same 25 gold questions, same scoring. Candidates:
  A2 — FTS5 over raw message text, AND→OR planner (rebuilt on day-1 corpus)
  B  — bge-small embeddings over raw message text (rebuilt on day-1 corpus)
  H  — RRF(A2, B)
  O  — ontology-graph retrieval: FTS over individual labels+attrs, hits expanded
       through ontology_relation to sessions (ranked by relation weight)
  OH — RRF(O, A2, B)
Directly answers: does the ontology layer relate to / improve the agent
retrieval workflow, measured on the new structure?
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
STOP = set(
    "a an the is are was were be been being do does did to of in on for with"
    " and or not what which who why how when where whose that this these those"
    " it its during every any can cant can't could should would will shall"
    " about into from as at by we our your my i you they them he she his her".split()
)


def terms(q):
    return [t for t in re.findall(r"[a-zA-Z0-9_./-]+", q.lower())
            if t not in STOP and len(t) > 2]


def planned_fts(conn, table, col_out, question, k=5):
    ts = terms(question)
    out = []
    for query in (" AND ".join(f'"{t}"' for t in ts), " OR ".join(f'"{t}"' for t in ts)):
        if not query:
            continue
        try:
            rows = conn.execute(
                f"SELECT {col_out} FROM {table} WHERE {table} MATCH ?"
                " ORDER BY rank LIMIT 300", (query,)).fetchall()
        except sqlite3.OperationalError:
            continue
        for (s,) in rows:
            if s not in out:
                out.append(s)
                if len(out) == k:
                    return out
    return out


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

    t0 = time.time()
    conn.executescript("""
      DROP TABLE IF EXISTS d1_fts;
      CREATE VIRTUAL TABLE d1_fts USING fts5(text, session_id UNINDEXED);
      INSERT INTO d1_fts(text, session_id)
        SELECT content, session_id FROM messages
        WHERE trim(coalesce(content,''))!='' AND length(content)<20000
          AND NOT (content LIKE '[tool:%' AND length(content)<120);
      DROP TABLE IF EXISTS d1_ont_fts;
      CREATE VIRTUAL TABLE d1_ont_fts USING fts5(text, ind_id UNINDEXED);
      INSERT INTO d1_ont_fts(text, ind_id)
        SELECT class || ' ' || label || ' ' || coalesce(attrs,''), id FROM ontology_individual;
    """)
    conn.commit()
    fts_build = time.time() - t0
    print(f"FTS builds: {fts_build:.1f}s")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    rows = conn.execute(
        "SELECT session_id, substr(content,1,2000) FROM messages"
        " WHERE trim(coalesce(content,''))!='' AND length(content)<20000"
        " AND NOT (content LIKE '[tool:%' AND length(content)<120)").fetchall()
    sess = np.array([r[0] for r in rows])
    emb_path = ROOT / "embeddings-day1.npy"
    t0 = time.time()
    if emb_path.exists():
        emb = np.load(emb_path); emb_build = -1.0
        assert emb.shape[0] == len(sess)
    else:
        emb = model.encode([r[1] for r in rows], batch_size=256,
                           normalize_embeddings=True).astype(np.float32)
        np.save(emb_path, emb); emb_build = time.time() - t0
    print(f"embeddings: {emb.shape} build {emb_build:.1f}s")

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

    def onto(question, k=5):
        inds = planned_fts(conn, "d1_ont_fts", "ind_id", question, k=20)
        if not inds:
            return []
        sc = {}
        for rank, iid in enumerate(inds):
            w = 1.0/(rank+1)
            if iid.startswith("session:"):
                sid = iid.split(":", 1)[1]
                sc[sid] = sc.get(sid, 0.0) + w
                continue
            for (subj,) in conn.execute(
                    "SELECT subject FROM ontology_relation WHERE object=? LIMIT 200", (iid,)):
                if subj.startswith("session:"):
                    sid = subj.split(":", 1)[1]
                    sc[sid] = sc.get(sid, 0.0) + w
        return [s for s, _ in sorted(sc.items(), key=lambda kv: -kv[1])][:k]

    results = []
    for q in gold:
        golds = set(q["gold"])
        a2 = planned_fts(conn, "d1_fts", "session_id", q["question"])
        b = vec(q["question"])
        o = onto(q["question"])
        h = rrf(a2, b)
        oh = rrf(o, a2, b)
        results.append({"id": q["id"], "type": q["type"],
                        "A2": score(golds, a2), "B": score(golds, b),
                        "O": score(golds, o), "H": score(golds, h),
                        "OH": score(golds, oh)})

    def agg(key, qtype=None):
        sel = [r for r in results if qtype is None or r["type"] == qtype]
        return (sum(r[key][0] for r in sel)/len(sel),
                sum(r[key][1] for r in sel)/len(sel))

    print("\n=== DAY-1 DB — recall@5 / MRR@5 (n=25) ===")
    for c in ("A2", "B", "O", "H", "OH"):
        a = agg(c)
        print(f"{c:>3}: all {a[0]:.2f}/{a[1]:.2f} | K {agg(c,'K')[0]:.2f} |"
              f" P {agg(c,'P')[0]:.2f} | R {agg(c,'R')[0]:.2f}")
    (ROOT / "bench-results-day1.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
