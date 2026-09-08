# Security Policy

## Supported versions

Session Weaver is pre-1.0. Security fixes target the current `0.2.x` line;
older releases are not maintained separately.

## Reporting a vulnerability

Please do not open a public GitHub issue for a security concern. Instead, use
[GitHub's private vulnerability reporting](https://github.com/NetDevAutomate/SessionWeaver/security/advisories/new)
for this repository, or email the maintainer listed on the
[GitHub profile](https://github.com/NetDevAutomate). Include what you found,
how to reproduce it, and the affected version or commit. We aim to acknowledge
reports within a few days; this is a small open-source project run by one
maintainer plus contributors, so please be patient with fix timelines.

## Security model, briefly

Session Weaver's maintained concept and ontology commands operate on local data. The
distribution also exposes upstream session tools, including SSH sync; installation downloads
dependencies, and an agent's selected provider receives any content sent to it. The skill
installer does not register or start an MCP service. Sensitive surfaces include:

- **The session store** (`~/.config/studyloop/sessions.db`) contains full
  coding-session transcripts — treat it like a credential store. This package
  opens it read-only for `doctor`, `recall` and `ontology status`; `winddown`, concept
  lifecycle/import operations and `ontology rebuild` deliberately write authoritative or
  derived state. Capture, repair and sync use the re-exported tools, whose model is in the
  [StudyLoop monorepo](https://github.com/NetDevAutomate/StudyLoop)
  (`packages/agent-session-tools`). Anything you sync between machines rides
  your own SSH trust. Scope, project and tombstone checks constrain concept recall and
  projection. Ontology diagnostics are derived from the selected store and must not be
  treated as scope-filtered recall evidence.
- **The skill installer** writes the hub under `~/.agents/skills/session-weaver`
  and creates links or explicit copies named `session-weaver` inside each harness's
  skills directory. It refuses to delete a real file or directory it did not
  create by default (reported as a conflict instead). Explicit `install --copy --force`
  can replace the named harness target. `uninstall` requires the exact ownership marker
  or a link resolving to the hub. `--dry-run` shows actions without touching disk.
- **Concept files** are inputs or disposable outputs, not a second source of truth.
  Wind-down validates exact citations before committing. Legacy OKF imports remain
  labelled `legacy-unbound` unless binding succeeds; old verification labels are not
  acceptance. Projection preserves unowned, edited or symlinked files as conflicts.

Forgetting is not guaranteed across native transcripts, other machines, backups or generated
notes. Ontology is excluded from delta sync, but the pinned whole-database seed can carry
derived tables; see the [documented sync boundary](README.md#ontology-boundary).

The `agent-session-tools` dependency is pinned to an exact StudyLoop commit in
`pyproject.toml`, so `uv tool install` of a given Session Weaver commit is
reproducible and cannot silently pick up new upstream code.
