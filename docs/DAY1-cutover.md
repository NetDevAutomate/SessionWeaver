# Day-1 sessions.db cutover — 2026-09-06 ~20:50 UTC (user-directed)

## What was done
1. Live DB checkpointed (WAL TRUNCATE) and renamed `~/.config/studyloop/sessions.db`
   → `sessions-orig.db` (verified after move: quick_check ok, 5,607 sessions /
   129,803 messages). No version suffix in any live filename, per user preference.
2. Fresh `sessions.db` created by the pinned `session-export` from today's native
   stores: **357 sessions / 24,002 messages in 4.7 s** — claude_code 229 (including
   nested `subagents/agent-*.jsonl`, the class that was historically lost), codex 75,
   kiro_cli 40, grok 6, opencode 4, pi 3. Schema v30 via 30 migrations. 13 native
   sessions skipped as empty by the exporter.
3. **Zero empty-content rows at birth** — the fresh corpus proves the exporter fix
   end-to-end (the old DB accumulated 86k such rows over months).
4. Day-1 ontology populated inside the new DB (`ontology_structural`, indexed,
   `extraction_version=tier1-day1-2026-09-06`): **2,019 entities in 0.2 s at $0** —
   357 project contexts (100% coverage), 1,545 file artifacts, 97 commands, 20 test runs.
5. Hook-path probe: `session-export --claude-only` against the new DB is a clean
   idempotent no-op (claude stays 229). Production hooks now write here.

## What agents see now
- Live context = ~last 4 days of claude + ~9 months of codex/kiro + opencode/pi/grok.
- Full history remains queryable at `sessions-orig.db` (read-only) and can be merged
  forward at any time with `session-repair --from-db sessions-orig.db` (rehearse on a
  copy first per the standing guard; note BL-2's reporting caveat for opencode/pi).

## Open choices for the user
1. Merge `sessions-orig.db` history into the new DB now, or run lean and merge later?
2. Same cutover on Mac B? (Its DB is cleaned but still the old lineage; no sync is
   configured, so no divergence pressure today.)
3. Claude's ~4-day native retention makes the BL-3 sweep job time-critical — every day
   without an enforced export loses claude sidechain history permanently.
