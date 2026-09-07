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
session-weaver install --dry-run        # show what would happen
session-weaver status                   # inspect current wiring
session-weaver doctor                   # check the session store + tools
```

The skill is installed **once** into the shared hub `~/.agents/skills/session-weaver/`.
Codex, OpenCode and pi read that directory natively; Claude, Kiro and Grok get a
symlink from their own skills directory into the hub — two links in a chain instead of
six drifting copies. `--copy` duplicates instead of symlinking if your setup dislikes
links; a real directory already in the way is reported as a conflict and never deleted.

## What the system does

1. **Capture (enforced)** — Stop hooks in every harness plus a 4-hourly launchd sweep
   run `session-export`; the exporter produces zero empty rows (measured at birth on a
   full rebuild) and captures subagent sidechains that were historically lost.
2. **Store (one SQLite file)** — `~/.config/studyloop/sessions.db`, WAL mode: sessions +
   messages + FTS + ontology tables. Multi-machine sync over ssh is idempotent and
   cannot resurrect deleted rows (verified by probe).
3. **Structure (tier-1 ontology, $0)** — deterministic entity resolution builds a real
   T-Box/A-Box: 7 classes, 6 typed properties, 13,384 individuals, 28,698 relations,
   0 domain/range violations, 0.2 s build.
4. **Distil (tier-2 wind-down → OKF)** — a capable model reads each session's FULL text
   and authors knowledge concepts (Decision/Finding/Problem/Preference/Procedure) as
   OKF v0.2 Markdown with provenance frontmatter: 348 sessions → 2,033 concepts,
   $17.23, zero failures after the output-budget fix.
5. **Serve** — fusion retrieval (OKF concepts + FTS AND→OR planner + embeddings, RRF)
   answers agent questions with session provenance. [`SKILL.md`](SKILL.md) tells agents
   how to read it well and write wind-down knowledge back.

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
uv sync            # resolves agent-session-tools from the pinned StudyLoop commit
uv run pytest      # 24 tests: installer semantics, CLI, harness registry, skill sync
uv run ruff check .
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
