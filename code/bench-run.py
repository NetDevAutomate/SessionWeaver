"""Storage PoC benchmark: candidate A (FTS5) vs B (embeddings) vs A+B (RRF hybrid).

All candidates index the SAME retrieval view (kind='conversation' AND dup=0)
from corpus-derived.db and answer the SAME gold.json questions. Scoring is
session-level: recall@5 = |top5 ∩ gold| > 0, plus MRR@5. Deterministic — no
LLM judging anywhere.

Run with the agent-session-tools tool python (has sentence-transformers+MPS).
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


def terms(question: str) -> list[str]:
    toks = re.findall(r"[a-zA-Z0-9_./-]+", question.lower())
    return [t for t in toks if t not in STOP and len(t) > 2]


def build_fts(conn: sqlite3.Connection) -> float:
    t0 = time.time()
    conn.executescript(
        """
        DROP TABLE IF EXISTS bench_fts;
        CREATE VIRTUAL TABLE bench_fts USING fts5(text, session_id UNINDEXED);
        INSERT INTO bench_fts(text, session_id)
          SELECT text, session_id FROM messages_derived
          WHERE kind='conversation' AND dup=0;
        """
    )
    conn.commit()
    return time.time() - t0


def fts_sessions(conn: sqlite3.Connection, question: str, k: int = 5) -> tuple[list[str], float]:
    q = " OR ".join(f'"{t}"' for t in terms(question)) or f'"{question}"'
    t0 = time.time()
    rows = conn.execute(
        "SELECT session_id FROM bench_fts WHERE bench_fts MATCH ?"
        " ORDER BY rank LIMIT 200",
        (q,),
    ).fetchall()
    out: list[str] = []
    for (s,) in rows:
        if s not in out:
            out.append(s)
            if len(out) == k:
                break
    return out, time.time() - t0




def fts_sessions_planned(conn: sqlite3.Connection, question: str, k: int = 5) -> tuple[list[str], float]:
    """AND-first (all terms), fall back to OR when AND yields < k sessions."""
    ts = terms(question)
    t0 = time.time()
    out: list[str] = []
    for query in (" AND ".join(f'"{t}"' for t in ts), " OR ".join(f'"{t}"' for t in ts)):
        if not query:
            continue
        rows = conn.execute(
            "SELECT session_id FROM bench_fts WHERE bench_fts MATCH ?"
            " ORDER BY rank LIMIT 200", (query,)).fetchall()
        for (s,) in rows:
            if s not in out:
                out.append(s)
                if len(out) == k:
                    return out, time.time() - t0
    return out, time.time() - t0

def score(golds: set[str], ranked: list[str]) -> tuple[int, float]:
    hit = int(any(s in golds for s in ranked))
    mrr = 0.0
    for i, s in enumerate(ranked):
        if s in golds:
            mrr = 1.0 / (i + 1)
            break
    return hit, mrr


def rrf(a: list[str], b: list[str], k: int = 5, c: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for lst in (a, b):
        for i, s in enumerate(lst):
            scores[s] = scores.get(s, 0.0) + 1.0 / (c + i + 1)
    return [s for s, _ in sorted(scores.items(), key=lambda kv: -kv[1])][:k]


def main() -> None:
    gold = json.loads((ROOT / "gold.json").read_text())
    conn = sqlite3.connect(DB)

    # ---------- Candidate A: FTS5 ----------
    fts_build = build_fts(conn)
    print(f"FTS build: {fts_build:.1f}s")

    # ---------- Candidate B: embeddings ----------
    from sentence_transformers import SentenceTransformer

    import os
    model_name = os.environ.get("EMB_MODEL", "all-MiniLM-L6-v2")
    model = SentenceTransformer(model_name)
    rows = conn.execute(
        "SELECT id, session_id, substr(text,1,2000) FROM messages_derived"
        " WHERE kind='conversation' AND dup=0"
    ).fetchall()
    ids = [r[0] for r in rows]
    sess = np.array([r[1] for r in rows])
    texts = [r[2] for r in rows]
    emb_path = ROOT / f"embeddings-{model_name.replace('/', '_')}.npy"
    t0 = time.time()
    if emb_path.exists():
        emb = np.load(emb_path)
        emb_build = -1.0
        assert emb.shape[0] == len(ids), "embedding cache stale — delete embeddings.npy"
    else:
        emb = model.encode(
            texts, batch_size=256, show_progress_bar=False, normalize_embeddings=True
        ).astype(np.float32)
        np.save(emb_path, emb)
        emb_build = time.time() - t0
    print(f"embeddings: {emb.shape} build {emb_build:.1f}s size {emb.nbytes/1e6:.0f}MB")

    def vec_sessions(question: str, k: int = 5) -> tuple[list[str], float]:
        t0 = time.time()
        qv = model.encode([question], normalize_embeddings=True).astype(np.float32)[0]
        sims = emb @ qv
        order = np.argsort(-sims)
        out: list[str] = []
        for idx in order:
            s = sess[idx]
            if s not in out:
                out.append(str(s))
                if len(out) == k:
                    break
        return out, time.time() - t0

    # ---------- run ----------
    results = []
    for q in gold:
        golds = set(q["gold"])
        a_rank, a_lat = fts_sessions(conn, q["question"])
        a2_rank, a2_lat = fts_sessions_planned(conn, q["question"])
        b_rank, b_lat = vec_sessions(q["question"])
        h_rank = rrf(a2_rank, b_rank)
        row = {
            "id": q["id"],
            "type": q["type"],
            "golds": len(golds),
            "A": score(golds, a_rank),
            "A2": score(golds, a2_rank),
            "B": score(golds, b_rank),
            "H": score(golds, h_rank),
            "a_lat": round(a_lat * 1000, 1),
            "b_lat": round(b_lat * 1000, 1),
        }
        results.append(row)
        print(
            f"{q['id']} {q['type']} golds={len(golds):>3} | "
            f"A hit={row['A'][0]} mrr={row['A'][1]:.2f} {row['a_lat']:>7}ms | "
            f"B hit={row['B'][0]} mrr={row['B'][1]:.2f} {row['b_lat']:>7}ms | "
            f"H hit={row['H'][0]} mrr={row['H'][1]:.2f}"
        )

    def agg(key: str, qtype: str | None = None):
        sel = [r for r in results if qtype is None or r["type"] == qtype]
        return (
            sum(r[key][0] for r in sel) / len(sel),
            sum(r[key][1] for r in sel) / len(sel),
            len(sel),
        )

    summary = {}
    for cand in ("A", "A2", "B", "H"):
        summary[cand] = {
            "all": agg(cand),
            "K": agg(cand, "K"),
            "P": agg(cand, "P"),
            "R": agg(cand, "R"),
        }
    fts_size = conn.execute(
        "SELECT sum(pgsize) FROM dbstat WHERE name LIKE 'bench_fts%'"
    ).fetchone()[0]
    out = {
        "results": results,
        "summary": summary,
        "build": {
            "fts_seconds": fts_build,
            "fts_bytes": fts_size,
            "emb_seconds": emb_build,
            "emb_bytes": int(emb.nbytes),
            "rows_indexed": len(ids),
        },
    }
    import os as _os
    (ROOT / f"bench-results-{_os.environ.get('EMB_MODEL','minilm').replace('/','_')}.json").write_text(json.dumps(out, indent=1))
    print("\n=== summary (recall@5 / MRR@5, n) ===")
    for cand in ("A", "A2", "B", "H"):
        s = summary[cand]
        print(
            f"{cand}: all {s['all'][0]:.2f}/{s['all'][1]:.2f} (n={s['all'][2]}) | "
            f"K {s['K'][0]:.2f}/{s['K'][1]:.2f} | P {s['P'][0]:.2f}/{s['P'][1]:.2f} | "
            f"R {s['R'][0]:.2f}/{s['R'][1]:.2f}"
        )
    print(f"\nbuild: fts {fts_build:.1f}s/{(fts_size or 0)/1e6:.0f}MB · "
          f"emb {emb_build:.1f}s/{emb.nbytes/1e6:.0f}MB · rows {len(ids)}")


if __name__ == "__main__":
    main()
