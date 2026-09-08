<p align="center">
  <img src="images/sessionweaver-icon.png" alt="Session Weaver" width="160" />
</p>

# Session Weaver

**Cross-harness session memory for coding agents** — one queryable, provenance-carrying
knowledge system built from the transcripts of six harnesses (Claude Code, Codex,
Kiro CLI, OpenCode, pi, Grok), proven with benchmark data, not assertion.

This repository is the **standalone distribution**: one `uv tool install` gives you the
full session toolchain, the agent skill, and the installer that wires the skill into
every harness. The session tools' source of truth lives in the
[StudyLoop monorepo](https://github.com/NetDevAutomate/StudyLoop)
(`packages/agent-session-tools`, consumed here as a pinned git dependency — never
forked), part of the wider [StudyLoop](https://www.studyloop.dev/) system.

## Install

```bash
# Everything: session tools + skill installer, isolated, on PATH
uv tool install --from git+https://github.com/NetDevAutomate/SessionWeaver session-weaver
# …or from a local checkout
uv tool install --from /path/to/session_weaver session-weaver
```

This puts on your PATH: `session-weaver` (skill installer & doctor) plus the re-exported
session tools `session-export`, `session-query`, `session-context`, `session-sync`,
`session-repair`, `session-maint`.

> Already have `agent-session-tools` installed as a uv tool (e.g. from a StudyLoop
> source checkout)? The `session-*` executables collide by design — this package is the
> standalone distribution of the same tools. Install with `--force` to let it take over,
> or keep your workspace install and use only the skill installer from here.

### Wire the skill into your harnesses

```bash
session-weaver install                  # all six harnesses
session-weaver install --harness kiro,claude
session-weaver install --copy             # copies only for non-hub readers
session-weaver install --copy --force     # replace each named copy target explicitly
session-weaver install --dry-run          # show what would happen
session-weaver status                     # inspect current wiring
session-weaver doctor                     # check the session store + ontology + tools
session-weaver ontology rebuild           # full rebuild of the default store
session-weaver ontology rebuild --incremental
session-weaver ontology status            # strictly read-only health diagnostics
session-weaver winddown --session ID --from winddown.json
session-weaver concept accept CONCEPT_ID --reason "reviewed"
session-weaver concept retire CONCEPT_ID --reason "obsolete"
session-weaver concept bind LEGACY_ID --from binding.json --reason "exact evidence"
session-weaver concept import-okf /path/to/okf --dry-run --report -
session-weaver concept project --out DIR [--project ID] [--db PATH] [--json]
session-weaver recall "<question>" [--k N] [--project ID] [--db PATH] [--json]
session-weaver bench audit-gold --db PATH
session-weaver bench run --db PATH [--gold docs/data/gold.json] [--k 5] [--json] [--out DIR] [--live-ro]
```

Concept writes target the same SQLite database as the session tools and return
content-free structured JSON. Legacy OKF import is recursive and deterministic: every
parseable frozen-writer record starts as `legacy-unbound`; canonicalizable legacy tag
case is reported and normalized to the A3a lowercase form while immutable identity
continues to hash the original bytes. The historical `machine-confirmed` label never
becomes acceptance. Only one literal occurrence of the **full Markdown body** in
scope-visible evidence creates a new proposed bound successor. Missing or hidden claimed
sessions retain their source URI with no fabricated session/FK and remain unavailable
until the real session becomes scope-visible and can be bound safely. Zero matches,
ambiguity, no visible evidence, and oversized citation bodies (over the 2,000
code-point exact-match limit) remain unbound. A claimed session's evidence body over
the bounded reader's per-evidence size limit is never loaded for exact-match binding;
that one record is classified `legacy-unbound`/`oversized_evidence` and the import
continues — it does not abort the batch. A session mixing an oversized and a
normal-sized body still binds against the normal-sized body when exactly one match
exists there. Source traversal and report replacement are anchored to securely opened
directory descriptors;
if report delivery fails after commit, the CLI returns exit 1 with truthful committed
write counters. `--dry-run` performs the same classification with zero writes, and
repeated imports add no rows or events. The sanitized disposable-copy receipt, including
all 2,033 imported records, is
[`legacy-okf-import-baseline.json`](docs/data/legacy-okf-import-baseline.json).

`concept project --out DIR` rebuilds disposable, scope-authorized Markdown from
authoritative concept state; it never writes database truth and never promotes trust.
One output directory is owned by exactly one scope/project selector through a
non-symlink `.session-weaver-projection.json` marker, and a
`.session-weaver-projection-manifest.json` binds every generated filename to its concept
ID and exact byte SHA-256. A previously-generated file is only ever deleted as stale
when all five conditions hold: it is listed in the prior valid manifest, its name matches
the generated-filename grammar, its embedded ownership marker matches the manifest's
concept ID, its current byte SHA-256 still equals the manifest's, and it is a regular,
non-symlink file opened through the held output-directory descriptor. Any other
unowned or externally modified file is preserved and reported as a conflict, never
deleted. Two invocations racing on the same `--out` fail closed: whichever one observes
the other's marker/manifest mid-publication reports a conflict rather than corrupting
either run; there is no lock. Every rendered file carries honest frontmatter —
`model_authorship: model-proposed` always, and `citation_binding: machine-confirmed`
only for bound roots (`absent` for legacy-unbound roots) — so a reader can tell
model-proposed interpretation from machine-confirmed citation binding at a glance.

`recall "<question>" [--k N] [--project ID] [--json]` answers with **concepts first, then
sessions**: a pure AND→OR planner (tokenize, drop stop words/short tokens, quote every
term, try the AND-joined query before falling back to OR) searches concepts through the
same scope-authorization seam `concept project` uses, then searches raw session text
through `messages_fts`, deduplicated against any session a returned concept already
cites. A legacy-unbound concept is returned labelled `legacy-unbound (session-level
provenance)` with no citations, never silently upgraded to a machine-confirmed one. The
report shape is frozen and schema-checked
([`recall-contract.json`](docs/data/recall-contract.json)). Recall ships with **no
embeddings** and never consults the tier-1 ontology tables — it is pure keyword search
over the same authoritative stores `concept project` reads.

`bench audit-gold --db PATH` verifies the frozen 25-question K11/P8/R6 corpus before
scoring. `bench run` evaluates at `k=5` and emits all-25 plus visibility-eligible-subset
recall@5/MRR@5 with Wilson 95% intervals, the same-visibility raw-text positive control,
a clearly non-gating unrestricted diagnostic, concept-candidate coverage, and a separate
corpus-verified directional paraphrase table. The pre-registered verdict is PASS at
overall recall ≥0.64 with floors K≥0.81/P≥0.15/R≥0.57, INVESTIGATE at 0.54–0.64 when
all floors pass, and FAIL below 0.54 or on any floor breach; these bands are not tuned
after measurement. A production exporter older than fix `7f9a19ec` labels the entire run
`pre-fix/provisional` and cannot close the stage. Ontology rebuild is parity preparation
only and never becomes a recall input. Live evaluation must use SQLite Online Backup;
`--live-ro` only permits an explicit read-only diagnostic of the live path.

The skill is installed **once** into the shared hub `~/.agents/skills/session-weaver/`.
Codex, OpenCode and pi read that directory natively; Claude, Kiro and Grok get a
symlink from their own skills directory into the hub — two links in a chain instead of
six drifting copies. `--copy` duplicates into only the non-hub-reading harnesses if your
setup dislikes links. An existing named target is a non-destructive conflict unless
`--force` is explicit; force replaces that target only. Installer-created copies carry
`.session-weaver-owned.json` containing `{"owner":"session-weaver","schema":1}`.
Uninstall removes a copied directory only when that exact marker proves ownership, and
removes a symlink only when it resolves to the SessionWeaver hub.

Ontology commands use `~/.config/studyloop/sessions.db` unless `--db PATH` is explicit.
Rebuild opens read/write and emits aggregate JSON only; status opens a SQLite read-only
URI, never creates the database, and exits nonzero for any unhealthy dimension.

## What the system does

1. **Capture** — `session-export` imports supported harness transcripts into the
   shared store, produces zero empty rows, and preserves subagent sidechains that
   were historically lost. Phase A does not install an automatic ontology rebuild.
2. **Store (one SQLite file)** — `~/.config/studyloop/sessions.db`, WAL mode: sessions +
   messages + FTS + ontology tables. Multi-machine sync over ssh is idempotent and
   cannot resurrect deleted rows (verified by probe).
3. **Structure (tier-1 ontology, $0)** — deterministic entity resolution builds a real
   T-Box/A-Box. The post-pin Online Backup baseline contains 7 classes, 6 typed
   properties, 13,336 individuals and 28,766 relations with zero domain/range
   violations; its cold full rebuild completed in 3.28 s. See the sanitized
   [`ontology-tier1-baseline.json`](docs/data/ontology-tier1-baseline.json).
4. **Distil (tier-2 wind-down → OKF)** — a capable model reads each session's FULL text
   and authors knowledge concepts (Decision/Finding/Problem/Preference/Procedure) as
   OKF v0.2 Markdown with provenance frontmatter: 348 sessions → 2,033 concepts,
   $17.23, zero failures after the output-budget fix.
5. **Serve** — fusion retrieval (OKF concepts + FTS AND→OR planner + embeddings, RRF)
   answers agent questions with session provenance. [`SKILL.md`](SKILL.md) tells agents
   how to read it well and write wind-down knowledge back.

### Ontology safety and sync boundary

The maintained opt-in acceptance test opens an explicit source read-only, records only
schema/count/freshness sentinels, creates a unique SQLite Online Backup under `/tmp`,
and runs rebuilds only against that disposable copy. The retained source content receipt
hashes that transaction-aligned Online Backup before any ontology mutation; it never
hashes the bare main file of a WAL database and never uses `cp`. Source sentinels are
compared again before the backup and sidecars are deleted. Retained evidence contains
counts, hashes, timings, and health verdicts only.

Ontology tables are absent from the pinned normal and global **delta-sync** allow lists.
The pinned first-time `_seed_remote_db` path still transfers an entire SQLite Online
Backup, so it can carry existing ontology tables. Phase B B2 owns seed sanitization and
destination-local rebuild; until then, do not interpret the delta exclusion as a claim
that ontology rows can never travel by any sync path.

## The measured result

25 corpus-verified benchmark questions, session-level recall@5 / MRR@5 (higher is
better; scoring definitions in [`docs/GLOSSARY.md`](docs/GLOSSARY.md)):

| Retrieval candidate | recall@5 / MRR@5 | Keyword | Paraphrase | Relational |
|---|---|---|---|---|
| Raw-text FTS (tuned) | 0.48 / 0.38 | 0.73 | 0.12 | 0.50 |
| Raw-text embeddings | 0.28 / 0.20 | 0.45 | 0.25 | 0.00 |
| Raw-text hybrid | 0.48 / 0.36 | 0.82 | 0.12 | 0.33 |
| **OKF wind-down store alone** | **0.64 / 0.50** | 0.91 | 0.25 | 0.67 |
| **OKF + hybrid fusion** | **0.68 / 0.50** | **1.00** | 0.25 | **0.67** |

The distilled knowledge layer adds **+0.20 recall over the best raw-text engine**.
Full method, per-question rows, threats to validity, and the measurement timeline:
[`docs/RESULTS-final.md`](docs/RESULTS-final.md) — read the reading-order note at the
top: the addenda **supersede** the interim recommendation in the base document
(extraction fidelity, not architecture, was the variable).

**Interactive architecture diagrams** (archify-delivered, open in a browser):
[`docs/architecture/final-architecture.html`](docs/architecture/final-architecture.html)
· [`docs/architecture/knowledge-pipeline.html`](docs/architecture/knowledge-pipeline.html)
— regenerable/diffable specs sit beside each HTML.

## Repository layout

```
src/session_weaver/   the installable package: skill installer, doctor CLI,
                      packaged SKILL.md (+ re-exported session-* entry points)
tests/                unit + integration tests for the package (pytest)
SKILL.md              the agent skill (canonical copy; shipped in the package)
code/                 frozen PoC evidence: the pipeline scripts that produced the
                      benchmark results (runnable, documented in-file, not maintained
                      as package code)
docs/                 RESULTS-final.md (all measurements) · GLOSSARY.md · findings
docs/architecture/    archify specs + delivered interactive HTML diagrams
docs/data/            gold.json answer key + raw per-question result rows
images/               logo/icon assets used by this README and GitHub
```

## Development

```bash
uv sync
uv run pytest -W error                # full suite; package coverage must stay >=90%
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The real-corpus ontology acceptance is opt-in and operates only on an Online Backup:

```bash
SESSION_WEAVER_ONTOLOGY_SOURCE=/absolute/path/to/sessions.db \
  uv run pytest tests/test_ontology_live.py::test_real_corpus_online_backup_acceptance \
  -m live -W error --no-cov
```

## Lineage

Built 2026-09-06 during the Session Weaver Phase 0 close-out and storage PoC; this repo
is the Phase 1 standalone packaging of that work. Production source of truth:
[StudyLoop](https://github.com/NetDevAutomate/StudyLoop) · site:
[studyloop.dev](https://www.studyloop.dev/). Costs are in the LiteLLM ledger (runs
`…-wpp3-ontology`, `…-t2-winddown`, `…-phase0-gate`).

## Contributing, conduct, security, license

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). This project follows
the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md); report security concerns privately
per [SECURITY.md](SECURITY.md). Changes are recorded in [CHANGELOG.md](CHANGELOG.md);
third-party attributions in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
MIT licensed — see [LICENSE](LICENSE).
