# Finding — the 86,108 non-searchable rows: forensics and disposition — 2026-09-06

Follow-up to `data-quality-audit.txt` at the user's direction: "we shouldn't have any
db noise — filter at ingest + clean existing, or repopulate from real harness data."
Every number below measured on the frozen corpus (5,617 sessions / 215,911 messages).

## Breakdown

| Class | Rows | What they are | Repopulatable? |
|---|---|---|---|
| A. role='unknown' | 26,314 | Transport events, never had conversational text: `progress` 24,608, `attachment` 1,523, `system` 182, fork-ref 1 | N/A — nothing to populate |
| B. Empty sidechain user/assistant | 59,372 | REAL subagent conversation turns whose text the old exporter dropped (metadata carries only envelope: cwd, agentId, sourceToolAssistantUUID — **no message body**) | See below |
| B1. — cwd `/Users/taylaand/...` | 49,411 | Old machine/username; no taylaand home on either Mac; native transcripts gone from Mac A and Mac B (checked both) | **NO — permanently lost** |
| B2. — cwd `/Users/ataylor/...` + other | ~9,961 | This machine; parent transcripts sampled 79: **1 found / 78 pruned** by Claude Code retention | **~99% NO** |
| B3. — September 2026 residue | 41 | Recent; parents likely still on disk | **YES — recover now before pruning** |

## The leak has already been fixed upstream

Empty-sidechain rows by month: 2026-07 = 30,725 (44% of that month's sidechain rows);
2026-08 = 1,584; **2026-09 = 41 vs 18,194 captured with text (99.8% capture)**. The
current exporter records sidechain content correctly; classes B1/B2 are historic damage
from an old exporter version, discovered only now because nothing measured
searchable-text coverage. (That absence is itself the lesson — validity metrics must be
first-class; see disposition 4.)

## Recommended disposition (per class)

1. **Class A (transport events): filter at ingest + quarantine existing.** Exporter
   stops writing `progress`-type events as message rows (or writes them typed as
   events); existing 26,314 rows are removed from `messages` after a hashed
   preservation packet (same discipline as the main-dirty-tree packet) — they retain
   value only as tool-flow telemetry, which the ontology layer (candidate D) can take
   from the packet if ever needed. This also closes the FTS lag (26,314 = exactly the
   FTS-skipped rows).
2. **Class B1/B2 (lost text): keep as tombstones, mark, exclude from retrieval.** These
   rows are evidence a subagent conversation happened (timestamps, agentId, project
   cwd — the ontology's ProjectContext wants them). Deleting them would fabricate a
   cleaner history than we actually have — the honest representation is
   `content_state='lost_at_source'`. They are excluded from FTS/embeddings/contracts by
   type, so they stop being noise to agents while remaining true to provenance.
3. **Class B3 (41 rows): recover immediately** via a repair backfill while parents
   exist, under the BL-2 live-apply guard (fresh .bak + disposable two-apply check).
4. **Go-forward guarantee (the real fix):** ingest-time classification contract — every
   exported row gets a `kind` (conversation / tool-event / lost) and searchable-text
   coverage becomes a doctor metric with a threshold alarm. This is BL-3's verification
   requirement extended to content quality: the July damage went unnoticed for two
   months because nothing measured coverage.

## Recovery search results (2026-09-06, follow-up at user's direction)

Full sweep for the 277 distinct affected parent sessions:
- Mac A `~/.claude/projects`: only 8 transcripts exist in total (aggressive retention
  pruning); **1/277** affected parents present.
- Mac B `~/.claude/projects`: 117 transcripts; **5/277** present.
- Disk-wide `mdfind` on Mac A for sample parent UUIDs: nothing outside the store.
- Time Machine: local snapshots reach back only to 2026-09-05; **a network TM
  destination exists (NAS `DXP4800GT`)** — older `~/.claude/projects` states for Macs
  A/B may exist in its history. HUMAN avenue: browse the NAS backup for
  `~/.claude/projects` at dates around 2026-07 (the damage peak).
- **Mac C (owner of user `taylaand`, source of 49,411 of the lost rows): cannot be
  connected to (initiate-only). PRIMARY recovery hope.** To check, run ON Mac C:
  `ls ~/.claude/projects | wc -l` and, if transcripts survive there, run the pinned
  `session-export` (after updating Mac C's wheel) or
  `session-repair --stage-output <path>` to produce a staging snapshot that can be
  carried to Mac A and merged with `session-repair --from-db`.

## Sync repopulation safety (user's question: will a sync re-add cleaned 'bad' rows?)

**Measured, on the gate-2b disposable pair over real ssh transport:** rows with empty
('') or NULL content planted on the peer and absent locally were NOT pulled back by
`sync all --incremental` NOR by `--reconcile`, while a non-empty control row planted in
the same session DID transfer. Code basis in the pinned wheel — `sync.py:586-590`: the
messages merge INSERT is generated with
`WHERE trim(coalesce(content,'')) != '' OR EXISTS(... same id at destination)` under the
comment "Do not resurrect empty exporter artifacts absent at the destination." Both push
and pull use this generator.

**Conclusion:** once the empty rows are deleted locally, peers running the PINNED wheel
cannot repopulate them — by design and by measurement. The one open risk is **Mac C
initiating a sync with an older wheel** whose merge SQL may predate this filter: Mac C
must be updated to the pinned wheel (or at least its sync verified ≥ this behaviour)
BEFORE it ever syncs against a cleaned database. Mac C is inbound-unreachable, so this
is a HUMAN step on Mac C.

## Clean procedure (proposed, NOT executed — destructive, needs go-ahead)

1. Preservation packet first (same discipline as WP-2): hashed dump of the rows to be
   removed (class A transport events + class B1/B2 tombstone candidates if the user
   chooses removal over tombstoning) — reversible by re-insert.
2. Rehearse on a disposable Online-Backup copy; verify FTS rebuild and counts.
3. Apply to Mac A live DB in a maintenance window (no hooks firing), then Mac B, then
   Mac C (HUMAN), each from its own packet; no `session-sync` runs until all three are
   clean AND Mac C's wheel is confirmed pinned.
4. Doctor gains a searchable-coverage metric so regression is visible immediately (BL-3).

Class B3 (41 recent recoverable rows) is repaired BEFORE the clean, while parents exist.

## Impact on the storage PoC

- claude_code's true searchable coverage of *recoverable* content is ~52.5% → after
  disposition it becomes ~100% of what exists, honestly labelled.
- WP-P0 (normalisation pass) implements exactly this classification on the corpus
  sidecar first — the PoC proves the disposition before any live change.
- Gold-standard questions must not depend on B1/B2 content (it does not exist anywhere).


## EXECUTED 2026-09-06 ~19:14 UTC (user-authorised: Macs A+B now, Mac C later)

Pre-checks that gated the live apply, all green:
- Rehearsal on an Online-Backup copy: 86,108 msgs + 10 empty sessions removed; pass 2 = 0/0;
  quick_check ok; FK 0; FTS rebuilt; fresh `session-export` into the cleaned copy added
  0 empty rows; live DB had 0 empty rows created since 2026-09-05 (current-wheel era).
- B3 (41 recent rows) recovery attempted first: repair preserved existing rows, and the
  sidechain bodies are absent from every transcript on Macs A and B (grep by uuid) —
  B3 is unrecoverable here too; rows were removed with the rest (in the packet).

| Machine | Removed msgs | Removed sessions | After (sessions/messages) | Integrity | Packet |
|---|---|---|---|---|---|
| Mac A (live) | 86,108 | 10 | 5,607 / 129,803 | quick_check ok, FK 0, FTS rebuilt, remaining_empty 0 | `~/.local/share/sessionweaver/clean-packets/macA-20260906/` (+ pre-clean .bak) |
| Mac B (live) | 98,786 | 10 | 5,511 / 124,663 | same; pass 2 = 0/0 | `~/.local/share/sessionweaver/clean-packets/macB-20260906/` (+ pre-clean .bak) |

Post-state Mac A: searchable coverage **100.0%**, FTS lag **0**, session-db MCP reads the
cleaned DB correctly (5,607/129,803). Clean corpus for the storage PoC:
`corpus-20260906-clean.db` (SHA in SHA256SUMS); the pre-clean corpus is retained as the
disposition record.

Mac C checklist (HUMAN, before it ever syncs): 1) check `~/.claude/projects` for
surviving taylaand transcripts (recovery snapshot via `session-repair --stage-output` if
so); 2) install the pinned wheel; 3) run `/tmp/clean-empty-rows.py`-equivalent with its
own packet; 4) only then permit sync. Cleaned peers cannot be repolluted by pinned-wheel
syncs (sync.py:586-590 + measured probes), so A/B are safe meanwhile.
