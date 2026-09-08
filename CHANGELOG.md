# Changelog

All notable public changes to Session Weaver are recorded here.

Session Weaver is a pre-release; the public API and installation experience
may change before `1.0.0`.

## [Unreleased]

## [0.2.0] - 2026-09-08

Session Weaver 0.2.0 packages the reviewed Phase A concept, recall, ontology, doctor, and
benchmark work with representative workflow coverage and exact installed-artifact release gates.
The release remains bounded by the documented INVESTIGATE result and Phase B exclusions.

### Added

- Added independent clean wheel and sdist installation provenance checks for every re-exported
  console script, including exact `session-export` resolution to CI-green StudyLoop commit
  `0adeb6c8ef958e453abe5b66d0bca39fdad6c309` descended from exporter fix `7f9a19ec`.
- Added representative fixture and live Online Backup workflow acceptance, pinned pre-commit CI,
  package receipts, exact tag/green-SHA release guard, and `docs/RELEASING.md`.

- Added A5 functional doctor controls: real known-term recall, exact concept-sidecar and
  full-tuple FTS digest verification, per-harness session-db MCP registration diagnostics,
  and Grok skill visibility. Session store, ontology, concept-sidecar, recall, and required
  executable failures are fatal; MCP registration and Grok skill absence are report-only.
- Added `docs/claims-audit.md`, mapping every testable README/SKILL claim to maintained code,
  tests, or retained evidence. Rewrote both byte-identical skill copies and README around the
  shipped concept-first/write-through-CLI behavior and the approved A6 INVESTIGATE values.
- Promoted the ontology Online Backup receipt to evidence v3 with the explicit
  `incremental_rebuild.candidate_sessions == 0` no-op proof.

- Added `session-weaver bench audit-gold --db PATH` and `session-weaver bench run
  --db PATH [--gold PATH] [--k 5] [--json] [--out DIR]`: a pre-registered,
  corpus-posture-gated recall benchmark over the frozen 25-question K11/P8/R6 set.
  It reports all-25 and visible-subset recall@5/MRR@5 with Wilson 95% intervals,
  same-visibility raw-text positive control, non-gating unrestricted diagnostic,
  concept-candidate coverage, fixed Q6 verdict bands/floors, and a separate
  40-question corpus-verified directional paraphrase set. Live-path use is refused
  unless explicitly read-only; maintained live proof runs exporter, ontology parity
  rebuild, OKF import, audit, and scoring only on a SQLite Online Backup and retains
  aggregate sanitized evidence.
- Added `session-weaver recall "<question>" [--k N] [--project ID] [--db PATH]
  [--json]` and the `recall()` seam: concept-first, AND→OR keyword recall over
  concepts (ranked by `bm25`, ties broken by full concept ID, authorized through
  the same seam `concept project` uses) followed by deduplicated raw-text
  session hits from `messages_fts`, excluding any session already cited by a
  returned concept. Legacy-unbound concepts are returned labelled
  `legacy-unbound (session-level provenance)` with empty citations, never
  silently upgraded. The AND→OR planner (tokenize, drop pinned stop words and
  short tokens, quote every term for FTS5) is ported verbatim from the
  storage-decision PoC. Ships with **no embeddings** and never consults the
  tier-1 ontology tables; the report shape is frozen and schema-checked
  against `docs/data/recall-contract.json`.
- Extracted the concept-selection/authorization logic `concept project` used
  into `authorization.authorized_concepts`, a single shared seam now used by
  both projection and recall so they can never disagree about which concept
  roots are visible under the caller's scope.
- Added `session-weaver ontology rebuild [--incremental] [--db PATH]` and
  read-only `session-weaver ontology status [--db PATH]` with deterministic,
  content-free JSON output and explicit success/unhealthy/usage exit semantics.
- Integrated complete ontology health diagnostics into `session-weaver doctor`
  through its existing read-only connection, including version, coverage,
  freshness, source-count, orphan, FK, domain/range, and hash checks.
- Added regression controls proving maintained ontology tables stay outside the
  pinned normal/global delta-sync allow lists and generated dump SQL. The
  first-time whole-database seed remains an explicit Phase B sanitization limit.
- Added an opt-in real-corpus acceptance harness that mutates only a unique
  SQLite Online Backup, proves source sentinels unchanged, guarantees cleanup,
  and retains the sanitized post-pin Tier-1 count/hash/timing baseline.
- Added the deterministic Tier-1 ontology core: canonical message normalization,
  exact frozen-PoC structural extraction, stable A-Box/T-Box identities,
  transactional full and incremental rebuilds, canonical logical hashing, and a
  read-only health model for later CLI and doctor integration.
- Established the Phase 2 test baseline with a 90% coverage gate and an isolated
  production-schema fixture that exercises upstream migrations and session capture.
- Added the transactional concept core behind `ConceptService`: strict bounded
  wind-down validation, exact scope-visible evidence binding, immutable bound and
  legacy roots, append-only deterministic lifecycle events, atomic legacy binding,
  and derived concept FTS consistency/rebuild support.
- Added the complete concept-write CLI and recursive legacy OKF importer: strict
  safe-YAML parsing, immutable byte identities, full-body conservative binding,
  deterministic content-free reports, dry-run parity, one-transaction writes,
  idempotent re-import, and atomic report files.
- Added `session-weaver concept project --out DIR [--project ID] [--db PATH]
  [--json]` and the `ConceptService.project` seam: a scope-authorized,
  deterministic Markdown projection rebuilt from authoritative concept state.
  Output ownership is scoped to one directory via a non-symlink marker and a
  manifest binding generated filenames to concept ID and exact byte SHA-256;
  stale managed files are removed only under a strict five-condition rule, and
  every write is descriptor-anchored, symlink-refusing, and atomic. Projection
  never writes DB truth, never promotes trust, and never mutates or rolls back
  concept state on failure.

### Fixed

- Ordered concept FTS consistency receipts by the complete canonical tuple rather than the
  non-unique concept ID alone, making corruption digests stable across duplicate-row insertion
  order.
- Split the ontology health orchestrator into private schema, source, build-state, version,
  coverage, integrity, freshness, and hash checks without changing the public status contract.
- Degraded oversized legacy OKF evidence bodies per record instead of aborting the
  whole import transaction: a claimed session's evidence body over the bounded
  reader's `MAX_BODY_CHARS` limit is now excluded from exact-match search and that
  one record is classified `legacy_unbound`/`oversized_evidence`, while every other
  record in the batch still imports. A session mixing an oversized and a
  normal-sized body still binds against the normal-sized body when exactly one
  match exists there; an ambiguous match among normal-sized rows still takes
  precedence over the oversized classification. The real-corpus live proof no
  longer pre-excludes any file: all 2,033 parseable OKF records import in one run.
- Preserved every parseable frozen-writer OKF record by reporting and lowercasing
  canonicalizable legacy tag case without changing original-byte identities; the
  disposable-copy receipt now reconciles all 2,033 roots and idempotent hits.
- Made unavailable legacy provenance privacy-safe: hidden and absent claimed sessions
  are report-indistinguishable, retain only their source URI with a nullable FK, and can
  produce a bound successor only after the real claimed session becomes scope-visible.
  Bound and wind-down roots still require a real session under sidecar schema v2.
- Validated import actor/project bounds and authorization scope before source scanning,
  returning exit-2 operation errors with reconciled zero counters.
- Anchored recursive OKF reads and atomic report replacement to securely opened directory
  descriptors, rejecting symlink components at open time and closing intermediate-swap
  races.
- Reported post-commit report-delivery failures as truthful partial success with durable
  write/import counters and `committed=true`, rather than falsely claiming zero writes.

- Made concept schema verification independent of SQLite row factories so the
  reviewed sidecar can be verified inside an `open_context` transaction.
- Closed temporary report descriptors when atomic report setup fails before file
  ownership transfers to the stream wrapper.
- Normalized valid JSON-escaped surrogate pairs from legacy YAML into Unicode
  scalars, rejected lone surrogates, and aligned legacy title validation with the
  frozen writer's actual 120-code-point limit rather than its prompt wording.

- Closed direct-SQL concept integrity gaps: bound roots now retain exact citation
  closure, lifecycle counters reject non-integer and exhausted values, legacy-bound
  successors must preserve every copied immutable field, and roots cannot commit
  without exactly one initial proposed lifecycle event.
- Rejected an explicit empty ontology `--db` as a usage error before any SQLite
  opener can select the default store, and made `None` the only default sentinel.
- Normalized legal non-text SQLite timestamp values into deterministic unhealthy
  diagnostics; status now returns a sanitized failure for unexpected health errors,
  while doctor records the failure and continues independent tool checks.
- Replaced the WAL-incomplete main-file source hash with an evidence-schema-v2
  receipt derived from the transaction-aligned SQLite Online Backup before ontology
  mutation; committed-WAL regression coverage proves the snapshot includes those frames.
- Made `install --copy` non-destructive by default: existing targets now
  conflict, while explicit `--force` replaces only the named harness target;
  Codex, OpenCode and pi continue reading the hub without duplicate copies.
- Added deterministic ownership markers to harness copies and the canonical
  hub; installation now preserves an existing unowned hub before any wiring,
  while uninstall preserves unowned hubs, copy directories, plain files, and
  symlinks that do not target the SessionWeaver hub.
- Closed SQLite connections owned by `session-weaver doctor` and its tests,
  preventing `ResourceWarning` failures under strict warning handling.

## [0.1.0] - 2026-09-07

### Added

- Standalone distribution: `uv tool install` of this repo provides the
  `session-weaver` CLI plus the re-exported session tools (`session-export`,
  `session-query`, `session-context`, `session-sync`, `session-repair`,
  `session-maint`). Implementations come from `agent-session-tools`, pinned
  as a git dependency on the
  [StudyLoop monorepo](https://github.com/NetDevAutomate/StudyLoop) —
  single source of truth, deliberately not forked.
- Skill installer (`session-weaver install`): installs the agent `SKILL.md`
  once into the shared hub `~/.agents/skills/session-weaver/` and symlinks it
  into the native skills directories of Claude, Kiro and Grok; Codex,
  OpenCode and pi read the hub directly. Idempotent, `--copy` and `--dry-run`
  supported, real files in the way are conflicts and are never deleted.
  `uninstall`, `status` and `doctor` (session-store + tools health check)
  complete the CLI.
- The Phase 0 storage-PoC evidence: pipeline scripts (`code/`), measured
  results with a reading-order/supersession note (`docs/RESULTS-final.md` —
  the tier-2 OKF wind-down store fused with hybrid retrieval, 0.68 recall@5,
  is the headline), benchmark gold set and raw rows (`docs/data/`), and
  interactive archify architecture diagrams (`docs/architecture/`).
- Community files: contributor guide, Contributor Covenant 2.1, security
  policy, third-party notices, MIT license.

[Unreleased]: https://github.com/NetDevAutomate/SessionWeaver/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/NetDevAutomate/SessionWeaver/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/NetDevAutomate/SessionWeaver/releases/tag/v0.1.0
