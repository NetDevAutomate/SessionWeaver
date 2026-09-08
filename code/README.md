# `code/` — frozen storage-PoC evidence

These are the scripts that produced the measurements in
[`docs/RESULTS-final.md`](../docs/RESULTS-final.md) (2026-09-06 storage PoC) and the
retained data files under [`docs/data/`](../docs/data/). They are **documented, runnable
history — not maintained package code**: `pyproject.toml` excludes this directory from
ruff and pyright, and several scripts carry their own ad-hoc dependencies (numpy, etc.)
that the package does not declare.

Do not edit these files, import them from `src/session_weaver/`, or "fix" their style;
their value is that they stay byte-for-byte what the evidence cites. Maintained
successors live in `src/session_weaver/` (`ontology.py`, `recall.py`, and the
`session-weaver bench` CLI). See CONTRIBUTING.md ("frozen PoC evidence") for the policy.

| Script | Role in the PoC (from each script's own docstring) |
| --- | --- |
| `wp-p0-derive.py` | WP-P0 normalisation sidecar: reads the frozen clean corpus, writes `corpus-derived.db` |
| `benchmark-questions.py` | The 25 gold-standard questions with SQL-LIKE evidence patterns |
| `bench-run.py` | Candidate A (FTS5) vs B (embeddings) vs A+B (RRF hybrid) benchmark |
| `bench-d.py` | Candidate D (ontology entities) plus D+H fusion benchmark |
| `bench-day1.py` | Same gold set re-run on the post-cutover DAY-1 DB structure |
| `bench-t2.py` | Candidate T2: the OKF wind-down store on the same gold set |
| `build-ontology.py` | Deterministic T-Box + A-Box ontology build inside `sessions.db` |
| `wp-p3b-structural-backfill.py` | D v1 tier-1 prototype: deterministic structural backfill |
| `wp-t2-winddown.py` | Tier-2 wind-down simulation producing the OKF v0.2 store |
| `clean-empty-rows.py` | Empty-row corpus cleaning with a preservation packet |
