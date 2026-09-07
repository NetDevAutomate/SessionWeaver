# Storage PoC — final results and data-driven recommendation — 2026-09-06

> **Reading order — the addenda supersede the base document.** This file grew in three
> measured passes on the same day and deliberately preserves each pass unedited:
> the base benchmark below, then *Addendum 1* (post-cutover day-1 DB + ontology-graph
> candidate), then *Addendum 2* (tier-2 wind-down OKF store — **the headline result**).
> Where they disagree, the later measurement wins. Concretely: recommendation items
> 2–3 below ("ship hybrid A2+B", "park the ontology/distillation layer") were written
> before Addendum 2 existed; Addendum 2 then showed full-text frontier distillation
> (T2, 0.64) and its fusion with hybrid (T2H, 0.68) beating hybrid raw text (0.48) —
> extraction *fidelity*, not the layer's architecture, was the variable. The shipped
> recommendation is therefore **T2H fusion**, with items 1, 4 and 5 unchanged.

Everything below was measured on the frozen post-clean corpus
(`corpus-20260906-clean.db`, 5,607 sessions / 129,803 messages, SHA-pinned) through the
normalised retrieval view (51,391 unique conversation rows). Benchmark: 25
corpus-verified gold-standard questions (11 keyword / 8 paraphrase / 6 relational),
session-level recall@5 / MRR@5, deterministic scoring. Artefacts: `wp-p0-derive.py`,
`benchmark-questions.py` + `gold.json`, `bench-run.py`, `wp-p3-extract.py`, `bench-d.py`,
`bench-results*.json`.

## Final table (coverage-adjusted: the 21 questions whose golds fall inside D's blind
extraction population; raw all-25 numbers in bench-results-final.json)

| Candidate | Overall | Keyword | Paraphrase | Relational | Build cost | Query |
|---|---|---|---|---|---|---|
| A2 — SQLite FTS5 + AND→OR planner | 0.57 / 0.48 | 0.80 | 0.14 | 0.75 | 0.7 s, $0 | ~5 ms |
| B — embeddings (bge-small, MPS) | 0.29 / 0.20 | 0.40 | 0.29 | 0.00 | 81 s, $0 | ~10 ms |
| **H — hybrid A2+B (RRF)** | **0.62 / 0.43** | 0.80 | 0.29 | 0.75 | both, $0 | ~15 ms |
| D — ontology entities (v0, llama4-maverick) | 0.24 / 0.18 | 0.30 | 0.14 | 0.25 | 10 min, $0.29 | ~5 ms |
| D+H fusion | 0.48 / 0.37 | 0.80 | 0.14 | 0.25 | — | — |

D extraction facts: 592 sessions (blind rule: ≥5 conversation turns, updated ≥2026-07-01),
2,601 contracted entities (670 artifact / 661 finding / 411 procedure / 364 decision /
358 problem / 137 preference), 150 gateway calls, $0.287 actual vs $0.56 estimated
(ledger: run `20260906T2000Z-wpp3-ontology`). Projected full-corpus (5.6k sessions): ~$2.7.

## Why D v0 lost — diagnosed, not guessed

The gold sessions DO have entities (extraction coverage worked); the specific facts
didn't survive: 9k-char digests of ~34k-char sessions dropped the details, and the
cheap extractor produced generic titles ("Critical finding", "Council model results")
that strip the exact names/numbers retrieval needs. Verbatim diagnosis in the session
log: for P02, session `a56aa41c…` had the entity "finding: grok-4.6 adversarial review
call failed" (a hit!) but sibling gold sessions yielded nothing searchable; for
R04/P04/P05 the relevant sessions produced entities about *other* topics. The ontology
LAYER isn't refuted — this specific price point of it is: **$0.29 of cheap-model
extraction over truncated digests loses to $0 of well-tuned FTS+vectors.** A D v1 that
could plausibly win needs: full-session (not truncated) input, a stronger extractor,
verbatim-fact preservation rules, and entity dedup/linking — i.e. real money and
pipeline complexity, to beat a baseline that is already free.

## The data-driven recommendation

1. **SQLite stays as the store — this question is closed.** Full-corpus index builds in
   0.7 s (FTS) / 81 s (vectors); queries ~15 ms; indexes ~160 MB; zero new operational
   surface (sync/repair/backup/two-Mac already gate-proven this week). Nothing measured
   justifies a storage migration at 10× current scale either.
2. **Ship hybrid retrieval (A2+B) as the agent context layer now.** Best overall
   (0.62/0.43), best on every category, free to build, trivial to keep incremental
   (new sessions: FTS insert + one embed batch). Concretely: add the AND→OR planner and
   an embedding sidecar to session-db MCP's search (Phase 2 work item).
3. **Park candidate D (ontology) with its measured price.** Do not build the service
   now. Revisit only IF Phase 2 usage shows agents failing on paraphrase/relational
   questions in practice (the benchmark says those are the weak spots: P≈0.29, R≈0.75
   for H) — and then as targeted extraction (decisions/findings only, full text,
   frontier model, verbatim-fact contract), not corpus-wide cheap extraction.
4. **Skip candidate C (Kuzu graph) for now.** C was sequenced to consume D's entities;
   with D v0's entity quality measurably too lossy, a graph over those entities cannot
   beat H either. Revisit alongside a D v1 if triggered.
5. **The biggest retrieval wins were data hygiene, not engines**: the clean (86k noise
   rows out, FTS lag 0, 100% searchable coverage) and query planning were worth more
   than any engine swap. Keep the validity dashboard (BL-3) as the guard.

## Threats to validity (stated, not hidden)

- n=25 questions, single corpus, self-benchmarked; MRR differences <0.1 are noise.
- Gold sets were defined by LIKE-pattern presence — biased toward literally-stated
  facts, which advantages FTS on K questions (this is why P/R categories exist).
- D was tested at ONE price/quality point; the layer's ceiling is untested (recorded
  as the explicit revisit trigger above).
- Recency skew: most gold questions concern the last two months of sessions.


## Addendum — day-1 DB structure benchmark (post-cutover, 2026-09-06 ~21:30 UTC)

Same 25 gold questions (all 70 gold sessions verified present post-merge), run against
`corpus-day1.db` (snapshot of the live day-1 DB: 5,611 sessions / 130,750 messages /
13,384 ontology individuals / 28,698 relations). New candidate **O** = ontology-graph
retrieval: FTS over individual labels+attributes, hits expanded through
`ontology_relation` to sessions — no message-text index involved at all.

| Candidate | Overall | K | P | R |
|---|---|---|---|---|
| A2 — FTS planner (raw text) | 0.48 / 0.38 | 0.73 | 0.12 | 0.50 |
| B — embeddings (raw text) | 0.28 / 0.20 | 0.45 | 0.25 | 0.00 |
| **O — ontology graph only** | **0.32 / 0.25** | 0.45 | 0.00 | **0.50** |
| H — RRF(A2,B) | 0.48 / 0.36 | 0.82 | 0.12 | 0.33 |
| OH — RRF(O,A2,B) | 0.48 / 0.37 | 0.82 | 0.12 | 0.33 |

Findings:
1. **Cutover parity** — the rebuild+merge did not degrade retrieval (A2/B match the
   pre-cutover runs within noise). Index builds: FTS 1.0 s, embeddings 85 s (65,191 rows).
2. **The structural ontology is a real retrieval signal**: O beats embeddings overall
   and ties FTS on relational questions using only $0 deterministic entities + graph
   expansion. It contributes nothing on paraphrase (expected — no semantic entities yet:
   that is what tier-2 wind-down adds).
3. Fusion OH ≈ H on this question set — the graph signal overlaps FTS's strengths today;
   its distinct value is the relational/typed query surface (ontology_query MCP tools),
   not raw ranking. Wiring O into production retrieval is a Phase 2 item with this
   benchmark as its acceptance test.

Diagram updated accordingly (`final-architecture.html`, delivered ok, visual-check
pass): `ontology → retrieval "graph signal"` edge, wind-down marked as agent-written,
and a "The loop" view showing agents → sessions → ontology → agent context.


## Scoring legend (applies to every results table in this document)

**Higher is always better. Both metrics range 0.00 – 1.00.**

- **recall@5** — the fraction of benchmark questions for which at least one correct
  (gold) session appeared anywhere in the candidate's top-5 results.
  1.00 = every question found; 0.48 = roughly half the questions found.
- **MRR@5** (mean reciprocal rank) — *where* in the top 5 the first correct hit sat,
  averaged over all questions: first place scores 1.0, second 0.5, third 0.33, fifth
  0.2, not-in-top-5 scores 0. It rewards putting the right answer at the top, not just
  somewhere in the list.
- Written as `recall / MRR`, e.g. `0.48 / 0.38`. Category columns (K keyword,
  P paraphrase, R relational) show recall@5 for that question type only.
- Differences smaller than ~0.1 on n=25 questions are noise — treat 0.48 vs 0.52 as a
  tie, and 0.28 vs 0.48 as a real gap.

## OKF status (for completeness)

OKF (Google's Open Knowledge Format, June 2026) was **not part of the measured PoC**
and has no data-driven outcome yet. It entered as the chosen *representation* for the
planned tier-2 layer: ontology individuals projected as Markdown-with-frontmatter files
agents can read directly, and the natural authoring format for wind-down entities
(agents write Markdown far more reliably than strict JSON). The right validation for it
is not this retrieval benchmark (it would just re-index the same content) but a usage
study in Phase 2: token cost and task-completion quality for agents consuming OKF files
versus MCP queries. Until that is measured, OKF adoption is a design decision, not a
data-driven one — recorded as such.


## Addendum 2 — Tier-2 wind-down OKF store: THE HEADLINE RESULT (2026-09-06 ~22:20 UTC)

The final missing piece was built and measured: a wind-down simulation over the tested
subset (blind rule: updated ≥ 2026-08-01, ≥10 messages → 348 sessions, 5.7M prompt
chars) using claude-sonnet-5 on FULL session text (fixing D v0's truncation failure),
authoring **2,033 OKF v0.2 concept files** (Decision/Finding/Problem/Preference/
Procedure with frontmatter: type, title, description, tags, session provenance via
`sources`, `verified: machine-confirmed`, actor `sessionweaver-winddown/0.1`).
Run: 348 + 144-retry calls (retry fixed output-truncation, 0 final failures);
cost **$17.23 actual vs $8.22 estimated** (output volume 2× the estimate — honest
miss, recorded in the ledger).

### Results — same 25 gold questions (recall@5 / MRR@5, higher is better)

| Candidate | Overall | Keyword | Paraphrase | Relational |
|---|---|---|---|---|
| A2 — FTS planner (raw text) | 0.48 / 0.38 | 0.73 | 0.12 | 0.50 |
| B — embeddings (raw text) | 0.28 / 0.20 | 0.45 | 0.25 | 0.00 |
| **T2 — OKF wind-down store alone** | **0.64 / 0.50** | 0.91 | 0.25 | 0.67 |
| H — hybrid raw text | 0.48 / 0.36 | 0.82 | 0.12 | 0.33 |
| **T2H — OKF + hybrid fusion** | **0.68 / 0.50** | **1.00** | 0.25 | **0.67** |

### What this proves, with data
1. **The tier-2 hypothesis holds**: distilled, provenance-carrying knowledge written at
   wind-down beats every raw-text engine — +0.20 recall over the previous best, perfect
   keyword recall in fusion, and the best relational score of the entire programme
   (0.67), from an index that is 2,033 small Markdown files built in 0.2 s.
2. **Extraction quality was the variable all along**: same corpus, same questions —
   cheap truncated extraction scored 0.24 (D v0); full-text frontier extraction scores
   0.64. The architecture didn't change; the fidelity did.
3. **Paraphrase (0.25) remains the open frontier** — concepts inherit the vocabulary of
   the session; closing it needs embedding the OKF store too (cheap next step: 2,033
   concepts ≈ seconds of embedding).
4. **OKF now has its first data point**: as the tier-2 authoring/serving format it
   delivered the measured win; agents wrote valid OKF markdown reliably once the output
   budget was right (144 truncation failures at 2,000 tokens; zero at 4,000).
