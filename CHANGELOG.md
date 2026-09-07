# Changelog

All notable public changes to Session Weaver are recorded here.

Session Weaver is a pre-release; the public API and installation experience
may change before `1.0.0`.

## [Unreleased]

### Added

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
