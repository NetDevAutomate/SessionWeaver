# Changelog

All notable public changes to Session Weaver are recorded here.

Session Weaver is a pre-release; the public API and installation experience
may change before `1.0.0`.

## [Unreleased]

### Added

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

### Fixed

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

[Unreleased]: https://github.com/NetDevAutomate/SessionWeaver/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/NetDevAutomate/SessionWeaver/releases/tag/v0.1.0
