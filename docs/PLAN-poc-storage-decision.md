# PLAN — PoC-driven storage & context-architecture decision (pre-Phase-2 gate)

Created 2026-09-06 at the user's direction: the SQLite-vs-alternatives decision must be
made **empirically, through PoCs run against the live corpus**, not through theoretical
council arbitration. Decision feeds Phase 2 (`PLAN-phase-2-standalone-product.md`) and
interacts with settled decisions #2 (one physical DB, namespaced ledger) and #3 (sync
hardening) — those stay settled; this plan decides the *retrieval/context architecture*
on top of, or instead of, the current store.

## 0. Decision statement

> Which storage/context architecture gives agents definitive, rich, reliable,
> verifiable historic-session context — measured on our own data — at an operational
> cost we can sustain (sync, repair, backup, two-Mac divergence)?

Non-negotiable requirements (user, 2026-09-06):
- Accuracy and verifiability outrank features: every answer an agent receives must be
  traceable to source rows (provenance), and data-validity warnings/errors must be
  first-class signals, not noise.
- Agent workflows lean heavily on historic sessions — retrieval quality on *our real
  questions* is the primary metric.

## 1. Fixed evaluation corpus (done)

- `~/.local/share/sessionweaver/poc-storage-decision/corpus-20260906.db` — SQLite Online
  Backup of the live DB, 2026-09-06: **5,617 sessions / 215,911 messages**, quick_check
  ok, SHA-256 in `SHA256SUMS`. All PoCs index THIS frozen corpus so results are
  comparable. The live DB is never touched.
- Second-Mac divergent corpus available if needed (Mac B backup
  `sessions.db.pre-gate2-20260906.bak`, 5,521 sessions / 223,449 messages) for
  cross-machine dedup/merge scenarios.

## 2. Data-quality audit (done — PoC input #1, drives the validity concern)

`data-quality-audit.txt` (same dir), highlights:

| Finding | Number | Implication |
|---|---|---|
| Orphan messages | **0** | Structural integrity is sound |
| Sessions with zero messages | 10 | negligible |
| Messages with NULL/empty content | 86,108 (~40%) | **The single biggest retrieval problem is upstream of any engine choice** |
| — of which role='unknown' (sidechain/progress plumbing) | 26,314 | transport noise, should be classified & excluded from retrieval |
| — empty user rows w/ metadata payload (sidechain events) | 47,461 | mostly orchestration events, NOT lost conversations (sampled) |
| claude_code searchable-text coverage | **52.5%** (94,750/180,436) | dominant source is half-opaque to FTS today |
| All other major sources searchable | 98–100% | codex/kiro/litellm fine |
| FTS lag vs messages | 26,314 rows | equals the unknown-role rows — FTS skips NULL content |
| content_hash NULL | 100% | no dedup/verification key exists yet (Phase 2 BL-1 interacts) |
| NULL seq | 4,882 | ordering fragile for those rows |
| Exact duplicate contents within a session | 1,508 groups | inflates retrieval hits |

**Consequence for the PoC design:** every candidate must include the SAME
normalisation/classification pass (knowledge vs transport-noise), or the comparison
measures data cleaning, not engines.

## 3. Candidates (build order = cheapest-first)

| ID | Candidate | What gets built | Key question it answers |
|---|---|---|---|
| A | **SQLite + FTS5 (baseline, tuned)** | Current schema; rebuild FTS including normalised content; exclude noise rows | How good is what we already own, once fed properly? |
| B | **SQLite + embeddings (hybrid)** | `sqlite-vec` (or LanceDB sidecar) over normalised messages; BM25+vector fusion à la ctx_search | Does semantic search close the recall gap on paraphrased questions? |
| C | **Embedded property graph** | Kuzu (embedded, Cypher) derived from corpus: Session/Message/Project/Tool/Topic/Decision nodes | Do relational traversals answer questions FTS/vectors provably cannot? |
| D | **Ontology service with data contracts** (user proposal 2026-09-06) | Typed semantic layer over the corpus: contracted entities (Decision, Struggle, TeachingMoment, ProjectContext, ToolOutcome…) each carrying provenance (source row ids), extraction version, confidence, freshness; served via MCP with schema-validated responses | Does a contracted knowledge layer beat raw-store queries for agent consumption — and does it make validity *enforceable* rather than hoped-for? |

Viability note on D (answering the "naive question" — it is not naive): the corpus
already carries the raw material (roles, sources, StudyLoop's 18 study tables, metadata
with cwd/gitBranch/agentId, handoff/decision documents), and the audit shows exactly why
a contract layer earns its keep — 40% of raw rows are transport noise that a contract
would exclude by type. D is **not mutually exclusive with A–C**: it is a consumption
layer that could sit on any engine. The PoC treats it as: SQLite stays system of record;
D's extraction pipeline materialises contracted entities into its own namespaced tables
(fits decision #2's ledger); agents query contracts, never raw rows. Its real costs are
what the PoC must measure: LLM extraction cost/latency over ~95k searchable messages
(sample first), ontology drift/maintenance, and contract-violation handling.

Explicit hybrid expectation to test, not assume: **A/B as durable+search layer, D on
top, C only if traversal questions demonstrably need it.**

## 4. Benchmark design (the decisive artefact)

1. **Question set (gold standard, ~40 questions)** drawn from REAL agent needs, each
   with a known answer and the source session(s) that contain it. Sources: today's
   handoff decisions ("what were the 8 settled decisions", "why is grok capture-only"),
   StudyLoop struggles ("what did I struggle with in SQL joins"), operational memory
   ("which ruff errors were in the rehearsal script", "what did sync retain on
   conflict"), cross-session synthesis ("what decisions were made about the sync
   tiebreak across sessions"). Mix: 15 keyword-friendly, 15 paraphrase/semantic, 10
   relational/multi-hop.
2. **Metrics per candidate:** recall@5 / MRR against gold sessions; answer-evidence
   found (binary, judged); provenance completeness (can the hit be traced to rows?);
   index build time; incremental update path (new session arrives — what must run?);
   storage overhead; query latency p50/p95; **sync/repair/backup story** (what breaks
   on the two-Mac divergence case — measured against the Mac B corpus for the winner).
3. **Judging:** deterministic scoring where possible (gold session-id match); LLM judge
   only for "evidence found" ties, via the gateway with the usual estimate/ledger rules.
4. **Report:** one table, all candidates, all metrics, plus a cost line. The decision
   drops out of the table or the PoC was designed wrong.

## 5. Work packages

- **WP-P0 — Normalisation & classification pass** (prereq for all): derive
  `retrievable_text` for every message (content, or extracted text from metadata where
  genuine), classify rows knowledge/noise, fix-forward seq gaps in the derived layer
  only. Zero writes to the corpus file; output is a sidecar `corpus-derived.db`.
- **WP-P1 — Candidate A** (tuned baseline): rebuild FTS over derived text; run benchmark.
- **WP-P2 — Candidate B**: embed derived text (local or titan-embed-v2; estimate first);
  hybrid rank; run benchmark.
- **WP-P3 — Candidate D** (ontology contracts): define v0 ontology (≤8 entity types) +
  JSON-schema contracts; LLM-extract over a stratified sample (e.g. 5k messages) +
  full pass over decision/handoff-bearing sessions; materialise; serve via a thin MCP
  tool; run benchmark. Record extraction $/1k messages for the scale-up estimate.
- **WP-P4 — Candidate C**: derive graph from WP-P0 + WP-P3 entities into Kuzu; author
  the 10 relational questions as Cypher; run benchmark.
- **WP-P5 — Operational drill**: for the leading candidate(s), replay the two-Mac
  divergence scenario and a repair cycle on the derived layer.
- **WP-P6 — Decision record**: results table → ADR (sessionweaver repo will number it);
  the user decides; losers' harnesses are kept as evidence.

Ordering rationale: A and B are days-cheap and establish the floor; D is the user's
architectural hypothesis and directly encodes the verifiability requirement; C is
built last because its unique value (multi-hop) is testable only once entities exist
(from D's extraction) — building C before D would duplicate extraction work.

## 6. Safety rules (inherited)

Live DB read-only always; all PoC work on the frozen corpus + sidecars; gateway calls
estimate-first with ledger reconciliation; the user commits; PoC code lives outside
worktree B (this dir + `~/.local/share/sessionweaver/poc-storage-decision/`) until the
decision lands, so Phase 1 extraction is not contaminated.

## 7. Interaction with BL-2 and data validity

The audit's findings (content_hash NULL everywhere, 40% non-searchable, dup groups,
seq NULLs) become acceptance criteria for Phase 2 regardless of engine choice: the
chosen architecture must (a) carry per-row provenance/hash, (b) classify noise at
ingest, (c) surface validity metrics in doctor (last-export lag, FTS/derived-layer lag,
contract-violation counts) so "warnings about the databases" become dashboards, not
surprises. BL-3 (verified hooks) feeds the same goal at the ingest end.
