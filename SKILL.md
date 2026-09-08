---
name: session-weaver
description: "Recall and record cross-session knowledge: retrieve concepts and source sessions with provenance, inspect session history, and write evidence-backed wind-down concepts. Use for past decisions, prior work, recurring struggles, session history, or substantial-session wind-downs."
---

# Session Weaver — cross-session memory for coding agents

Session Weaver reads captured sessions and evidence-backed concepts from one SQLite store,
`~/.config/studyloop/sessions.db`. Concepts are model-proposed interpretations; exact citation
binding is checked separately. Tier-1 ontology data is derived diagnostics and is not a recall
input.

## Installation and release boundary

An unqualified pre-merge Git install follows the repository default branch. During review use a
reviewed local checkout or explicitly pinned revision; only a post-merge install from the default
branch contains merged A7 work. `uv tool install --force` can replace executable links owned by a
separate `agent-session-tools` installation, so choose one owner deliberately. Source-tree tests
do not prove installed artifacts; release evidence installs the wheel and sdist independently.

The standalone installer does not provision user-managed `session-export` freshness automation,
MCP registration, or MCP liveness. Doctor's MCP checks are a report-only diagnostic, not
installed-package certification or server-liveness proof. Phase B's StudyLoop retrofit,
seed-snapshot sanitization, cross-machine concept replication or convergence, propagated
forgetting, embeddings, ontology-backed recall, and target-band quality are excluded from `0.2.0`.

## Read path

### 1. Recall concepts, then source sessions

Run the maintained concept-first CLI first:

```bash
session-weaver recall "<question>" --k 5 --json
```

When a harness exposes the same operation as `memory_recall`, use that surface instead. Recall
uses keyword AND→OR planning, returns concepts before deduplicated session hits, applies scope and
tombstone policy, ships no embeddings, and never queries the ontology.

Trust the two labels independently:

- `model_authorship: model-proposed` means a model authored the interpretation; it is not human
  approval.
- `citation_binding: machine-confirmed` means the exact quoted text was bound to visible evidence.
- `legacy-unbound` means imported historical material has only session-level provenance. Bind it
  through the CLI before acceptance; do not infer an exact citation.

### 2. Expand with the session-db MCP server when registered

After recall identifies a session or when raw transcript detail is needed:

- `session_search(query=...)` finds matching captured messages.
- `session_context(session_id=..., format="compressed")` retrieves a bounded excerpt.
- `session_hotspots(days=...)` reports recently touched files and projects.

These tools exist only when the harness has the session-db MCP server registered.
Claude Code has no session-db MCP server registered until StudyLoop's installer adds it; this
standalone Session Weaver installer installs the skill but does not register MCP servers. Use the
`session-query` / `session-context` CLIs when MCP is unavailable and scope is configured.

### 3. Inspect ontology health, never use it as recall evidence

```bash
session-weaver ontology status
```

This is a strictly read-only diagnostic. Ontology rows are derived locally and excluded from
normal/global delta sync. The pinned first-time whole-file seed can still carry existing ontology
rows until Phase B B2 sanitizes seed snapshots, so do not claim they can never travel by any sync
path.

## Write path — code-enforced wind-down

At the end of a substantial session, create `winddown.json` and submit it through the maintained
writer:

```bash
session-weaver winddown --session ID --from winddown.json
```

The JSON contract is exact: the top level contains only `concepts`; it holds 0–8 concepts. Every
concept contains exactly `type`, `title`, `description`, `tags`, `confidence`, and `quotes`.
`type` is one of `Decision`, `Finding`, `Problem`, `Preference`, or `Procedure`; titles contain at
most 12 words; tags contain 2–5 lowercase canonical values; confidence is 0.5–1.0; and every
concept has 1–8 verbatim quotes. A quote contains `quote` and may include the complete locator
triple `evidence_id`, `start`, and `end`.

```json
{
  "concepts": [
    {
      "type": "Decision",
      "title": "Use bounded online backups",
      "description": "SQLite live-store validation uses Online Backup rather than copying the main file.",
      "tags": ["session-memory", "sqlite"],
      "confidence": 0.9,
      "quotes": [
        {"quote": "Never copy the live DB with cp; use SQLite Online Backup."}
      ]
    }
  ]
}
```

The writer validates the whole batch, resolves every quote against scope-visible evidence, assigns
identities, writes authoritative DB state transactionally, and returns content-free structured
JSON. Authoritative rule: never hand-write OKF files; Markdown is a disposable projection, not
authoritative state. Use `session-weaver concept project --out DIR` to regenerate a projection.

## Measured posture — A6

On the post-fix/eligible `fb606468` exporter corpus at k=5, the all-25 and 25/25
visibility-eligible results were identical:

| Category | Recall@5 | Wilson 95% CI | MRR@5 | Wilson 95% CI |
| --- | ---: | --- | ---: | --- |
| Overall | 0.600000 | [0.407391, 0.765969] | 0.463333 | [0.286163, 0.650272] |
| K | 0.818182 | [0.523014, 0.948633] | 0.621212 | [0.341058, 0.838617] |
| P | 0.250000 | [0.071478, 0.590730] | 0.156250 | [0.032809, 0.502727] |
| R | 0.666667 | [0.299988, 0.903231] | 0.583333 | [0.241074, 0.860536] |

Recall@5 is `0.600000`, every fixed category floor passed, overall remained below the `0.64` target, and the verdict is **INVESTIGATE**. The overall Recall@5 interval remains wide at
`[0.407391, 0.765969]`. Current 25/25 visibility is **not directly comparable** to the frozen
PoC's 22 IDs, and the evaluated concept corpus consisted of 2,033 imported legacy-unbound roots;
those are recall-visible historical signal, not accepted or exact-cited assertions. The published
MRR intervals are generalized score intervals over reciprocal-rank values, not exact binomial
confidence intervals. The same-visibility raw-text positive control was **PASS** at 0.480000
recall / 0.312000 MRR.

The separate corpus-verified directional P set returned zero hits across 40 queries comprising five paraphrases for each of eight target-fact clusters; it is non-gating, and its Wilson 95% interval [0.000000, 0.087625] is a query-level calculation assuming independent queries, not a cluster-aware eight-target interval.
This is the retained **0/40** directional result.

Tier-1 ontology rebuild was parity preparation only and was never a recall input.

Historical context only: the earlier PoC reported concept-only 0.64/0.50 and unshipped fusion
0.68/0.50. Those values are not the current measured gate and do not describe shipped fusion or
embedding behavior.

## Stale-context checks and remediations

Stale context produces confident answers from an out-of-date world. Run these checks before
trusting recall for time-sensitive questions, and whenever results look older than expected:

1. **Store freshness.** `session-weaver doctor` prints the newest exported session's timestamp
   as `ok    session store fresh` or, once it is older than 7 days, `INFO  session store stale`.
   Remediation: run `session-export`. A capture gap means missing history; it does not prove
   nothing happened.
2. **Ontology staleness.** `session-weaver ontology status` reports freshness and
   extraction-version drift. Remediation: `session-weaver ontology rebuild`. Ontology rows are
   diagnostics, never recall evidence.
3. **Skill staleness.** Doctor compares `~/.agents/skills/session-weaver/SKILL.md` with the
   installed package's copy and prints `ok    hub skill current` or `INFO  hub skill stale`.
   Remediation: `session-weaver install`.
4. **Recalled-claim currency.** Before acting on a recalled concept or session, check its source
   date and whether the claim still applies to the present project and version; prefer the
   newest evidence when concepts disagree.

The store-age and hub-skill probes are report-only diagnostics and do not change the exit
code. Ontology health failures remain fatal in doctor. Store age measures session timestamps,
not the time of the last successful export. These two probes are newer than the `v0.2.0` tag;
use a checkout or commit containing the Unreleased changes.

## Freshness and safety rails

- `session-export` is run by the user's own hooks or sweep configuration. This installer does not
  provision that automation, so verify local freshness rather than assuming a schedule.
- Treat the live `sessions.db` as read-only outside maintained commands. Exports use
  `session-export`; concept writes use `winddown` / `concept`; ontology writes use explicit
  `ontology rebuild`.
- Never copy a WAL-mode live database with `cp`; use SQLite Online Backup.
- Never edit harness-native transcript stores such as `~/.claude/projects`.
- Cross-machine sync can move retained data. Do not promise propagated forgetting across original
  transcripts, peers, backups, or exported notes.
