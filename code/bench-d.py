"""Benchmark candidate D (ontology entities) on the same gold set, plus D+H fusion.

D retrieval: FTS5 over entity title+detail (AND-first, OR fallback), ranked
entities mapped to their provenance session_ids, deduped to top-5 sessions.
Also replays A2 (planned FTS over raw messages) and B (bge-small embeddings,
cached) to produce the final all-candidates table, including the full fusion
H(A2+B) and D+H. Reports raw scores over all 25 questions AND coverage-adjusted
scores over the 21 questions whose golds intersect D's blind population.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np

ROOT = Path.home() / ".local/share/sessionweaver/poc-storage-decision"
DB = ROOT / "corpus-derived.db"
STOP = set(
    "a an the is are was were be been being do does did to of in on for with"
    " and or not what which who why how when where whose that this these those"
    " it its during every any can cant can't could should would will shall"
    " about into from as at by we our your my i you they them he she his her".split()
)
RULE = "n_conv>=5 AND updated_at>='2026-07-01'"


def terms(question: str) -> list[str]:
    toks = re.findall(r"[a-zA-Z0-9_./-]+", question.lower())
    return [t for t in toks if t not in STOP and len(t) > 2]


def planned(conn, table, question, k=5):
    ts = terms(question)
    out: list[str] = []
    for query in (" AND ".join(f'"{t}"' for t in ts), " OR ".join(f'"{t}"' for t in ts)):
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


def score(golds, ranked):
    hit = int(any(s in golds for s in ranked))
    mrr = 0.0
    for i, s in enumerate(ranked):
        if s in golds:
            mrr = 1.0 / (i + 1)
            break
    return hit, mrr


def rrf(*lists, k=5, c=60):
    scores: dict[str, float] = {}
    for lst in lists:
        for i, s in enumerate(lst):
            scores[s] = scores.get(s, 0.0) + 1.0 / (c + i + 1)
    return [s for s, _ in sorted(scores.items(), key=lambda kv: -kv[1])][:k]


def main() -> None:
    gold = json.loads((ROOT / "gold.json").read_text())
    conn = sqlite3.connect(DB)
    pop = set(r[0] for r in conn.execute(f"SELECT id FROM sessions_derived WHERE {RULE}"))

    # message FTS (A2) — rebuild if missing
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='bench_fts'").fetchone():
        conn.executescript(
            "CREATE VIRTUAL TABLE bench_fts USING fts5(text, session_id UNINDEXED);"
            "INSERT INTO bench_fts(text, session_id) SELECT text, session_id FROM"
            " messages_derived WHERE kind='conversation' AND dup=0;")
        conn.commit()
    # entity FTS (D)
    conn.executescript(
        "DROP TABLE IF EXISTS ont_fts;"
        "CREATE VIRTUAL TABLE ont_fts USING fts5(text, session_id UNINDEXED);"
        "INSERT INTO ont_fts(text, session_id) SELECT type || ': ' || title || '. ' ||"
        " detail, session_id FROM ontology_entities;")
    conn.commit()

    # embeddings (B) — cached
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    rows = conn.execute("SELECT session_id FROM messages_derived WHERE"
                        " kind='conversation' AND dup=0").fetchall()
    sess = np.array([r[0] for r in rows])
    emb = np.load(ROOT / "embeddings-BAAI_bge-small-en-v1.5.npy")
    assert emb.shape[0] == len(sess)

    def vec(question, k=5):
        qv = model.encode([question], normalize_embeddings=True).astype(np.float32)[0]
        order = np.argsort(-(emb @ qv))
        out = []
        for idx in order:
            s = str(sess[idx])
            if s not in out:
                out.append(s)
                if len(out) == k:
                    break
        return out

    results = []
    for q in gold:
        golds = set(q["gold"])
        a2 = planned(conn, "bench_fts", q["question"])
        b = vec(q["question"])
        d = planned(conn, "ont_fts", q["question"])
        h = rrf(a2, b)
        dh = rrf(d, a2, b)
        covered = bool(golds & pop)
        results.append({
            "id": q["id"], "type": q["type"], "covered": covered,
            "A2": score(golds, a2), "B": score(golds, b), "D": score(golds, d),
            "H": score(golds, h), "DH": score(golds, dh)})
        r = results[-1]
        print(f"{q['id']} {q['type']} cov={int(covered)} | " + " | ".join(
            f"{c} {r[c][0]}/{r[c][1]:.2f}" for c in ("A2", "B", "D", "H", "DH")))

    def agg(key, qtype=None, covered_only=False):
        sel = [r for r in results if (qtype is None or r["type"] == qtype)
               and (not covered_only or r["covered"])]
        if not sel:
            return (0, 0, 0)
        return (sum(r[key][0] for r in sel) / len(sel),
                sum(r[key][1] for r in sel) / len(sel), len(sel))

    print("\n=== recall@5 / MRR@5 — ALL 25 questions ===")
    for c in ("A2", "B", "D", "H", "DH"):
        a = agg(c)
        print(f"{c:>3}: all {a[0]:.2f}/{a[1]:.2f} | K {agg(c,'K')[0]:.2f} |"
              f" P {agg(c,'P')[0]:.2f} | R {agg(c,'R')[0]:.2f}")
    print("=== coverage-adjusted (21 questions inside D population) ===")
    for c in ("A2", "B", "D", "H", "DH"):
        a = agg(c, covered_only=True)
        print(f"{c:>3}: all {a[0]:.2f}/{a[1]:.2f} (n={a[2]}) | K {agg(c,'K',True)[0]:.2f} |"
              f" P {agg(c,'P',True)[0]:.2f} | R {agg(c,'R',True)[0]:.2f}")
    (ROOT / "bench-results-final.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
