---
name: session-weaver
description: "Recall and record cross-session knowledge: retrieve concepts and source sessions with provenance, inspect session history, and write evidence-backed wind-down concepts. Use for past decisions, prior work, recurring struggles, session history, or substantial-session wind-downs."
---

# Session Weaver — cross-session memory for coding agents

Session Weaver reads captured sessions and evidence-backed concepts from one SQLite store,
`~/.config/studyloop/sessions.db`. Concepts are model-proposed interpretations; exact citation
binding is checked separately. Tier-1 ontology data is derived diagnostics and is not a recall
input.

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

The verdict is **INVESTIGATE**: every category floor passed, but overall recall remained below the
0.64 target. Current 25/25 visibility is **not directly comparable** to the frozen PoC's 22 IDs.
The same-visibility raw-text positive control was **PASS** at 0.480000 recall / 0.312000 MRR. A
separate corpus-verified directional P set scored **0/40** (0.000000 recall / 0.000000 MRR); it is
a **non-gating** warning. Tier-1 ontology rebuild was parity preparation only and was never a
recall input.

Historical context only: the earlier PoC reported concept-only 0.64/0.50 and unshipped fusion
0.68/0.50. Those values are not the current measured gate and do not describe shipped fusion or
embedding behavior.

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
