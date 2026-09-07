---
name: session-weaver
description: "Recall and record cross-session knowledge: search past coding sessions from six harnesses (Claude Code, Codex, Kiro, OpenCode, pi, Grok), retrieve decisions/findings with provenance from the OKF knowledge store, and write wind-down knowledge at session end. Use when the user asks 'what did we decide about…', 'have I worked on… before', 'what did I struggle with…', when starting work on a project that likely has history, or when ending a substantial session (wind-down). Triggers: session memory, past sessions, previous work, what did we decide, session history, wind-down, knowledge store."
---

# Session Weaver — cross-session memory for coding agents

One SQLite store (`~/.config/studyloop/sessions.db`) holds every session from every
supported harness on every synced machine, plus a typed ontology and an OKF knowledge
store of distilled Decisions/Findings. This skill tells you how to READ it well and
WRITE to it at wind-down. Benchmarked guidance, not vibes: fusion of the OKF store with
raw-text search scored 0.68 recall@5 vs 0.48 for raw text alone — **always query the
knowledge store first, raw text second.**

## Reading — answer "what do we know?" questions

### 1. First stop: the session-db MCP server (if connected)
- `session_search(query=…)` — full-text over messages; supports AND/OR/NOT.
- `session_context(session_id=…, format="compressed")` — token-efficient excerpt once
  you've found the right session; never pull full sessions unless asked.
- `session_hotspots(days=…)` — which files/projects recent sessions touched.

### 2. Knowledge store (OKF): distilled, provenance-carrying facts
Concept files: `~/.local/share/sessionweaver/poc-storage-decision/okf-store/`
(plain Markdown + YAML frontmatter — grep/read directly, no tooling needed):
```bash
grep -rl --include='*.md' -i "<topic>" <okf-store>/ | head
```
Each hit gives `type` (Decision/Finding/Problem/Preference/Procedure), a
self-contained description with verbatim names/numbers, and a `sources:` line
naming the origin session — follow it with `session_context` for the full story.
**Trust rules:** frontmatter `verified: machine-confirmed` means model-distilled, not
human-reviewed; `confidence` < 0.7 deserves a raw-text cross-check before you rely on it.

### 3. Ontology: typed/relational questions
For "which sessions touched file X", "what ran in project Y", "which subagents did
session Z spawn" — query the ontology tables directly (read-only!):
```bash
sqlite3 -readonly ~/.config/studyloop/sessions.db \
  "SELECT r.subject FROM ontology_relation r JOIN ontology_individual o ON o.id=r.object
   WHERE r.predicate='touched' AND o.label LIKE '%<file>%' LIMIT 10;"
```
Classes: Project, Harness, Session, SubagentSession, Artifact, Command, TestRun.
Properties: ranIn, conductedBy, childOf, touched, executed, produced.

### Query strategy (measured)
1. Specific terms beat sentences: extract 2-4 distinctive terms (paths, error strings,
   tool names) — AND them first, fall back to OR.
2. Check the OKF store AND raw search; they win on different question shapes
   (concepts: keyword/relational; raw text: verbatim details).
3. Always report provenance (session id + date) with any recalled fact, and say
   which layer it came from.

## Writing — the wind-down step (this is how the store stays good)

At the END of any substantial session (real decisions made, problems solved, findings
established), distil what THIS session knows while you still have full context:

1. Emit 0-8 concepts, types Decision | Finding | Problem | Preference | Procedure.
2. Rules that made the measured difference: only what the transcript supports; keep
   exact names/numbers/paths/commands VERBATIM; titles ≤12 words and specific (never
   "Critical finding"); descriptions 1-3 self-contained sentences.
3. Write each as an OKF file in the store, kebab-case filename, frontmatter:
```yaml
---
type: Decision
title: "…"
description: "…"
tags: [lowercase, topic, tags]
sources:
  - resource: sessionweaver://session/<this-session-id>
    role: transcript
verified:
  status: machine-confirmed
  by: <your-agent-name>/<version>
confidence: 0.9
actor: <your-agent-name>/<version>
---
<description body>
```
4. If you cannot know your session id, still write the concept and set
   `resource: sessionweaver://session/unknown` with a `date:` field — provenance
   degraded beats knowledge lost.

## Safety rails (absolute)
- The live `sessions.db` is READ-ONLY to you outside the ontology/OKF write paths:
  use `sqlite3 -readonly`; never UPDATE/DELETE sessions or messages; exports happen
  via `session-export`, not by you.
- Never copy the live DB with `cp` (WAL mode) — `.backup` only.
- Do not edit files under the harness native stores (`~/.claude/projects`, etc.).

## What to expect
- Recall quality: ~2 in 3 "what do we know" questions surface the right session in the
  top 5 (0.68 measured); paraphrase-style questions are the known weak spot (0.25) —
  if a reworded query misses, retry with the domain's literal vocabulary.
- The store spans machines: content synced from other Macs appears here; deleted noise
  cannot be resurrected by sync (verified).
- Freshness: hooks + a 4-hour sweep capture sessions automatically; a session ended
  minutes ago may not be exported yet.
