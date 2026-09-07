# Security Policy

## Supported versions

Session Weaver is pre-1.0. Security fixes target the `0.1.x` line only — there
is no older release to backport to.

## Reporting a vulnerability

Please do not open a public GitHub issue for a security concern. Instead, use
[GitHub's private vulnerability reporting](https://github.com/NetDevAutomate/SessionWeaver/security/advisories/new)
for this repository, or email the maintainer listed on the
[GitHub profile](https://github.com/NetDevAutomate). Include what you found,
how to reproduce it, and the affected version or commit. We aim to acknowledge
reports within a few days; this is a small open-source project run by one
maintainer plus contributors, so please be patient with fix timelines.

## Security model, briefly

Session Weaver is local-only: it runs no network service and makes no outbound
requests. Its two sensitive surfaces are:

- **The session store** (`~/.config/studyloop/sessions.db`) contains full
  coding-session transcripts — treat it like a credential store. This package
  only ever opens it read-only (`session-weaver doctor`); writes happen through
  the `session-*` tools it re-exports, whose threat model is documented in the
  [StudyLoop monorepo](https://github.com/NetDevAutomate/StudyLoop)
  (`packages/agent-session-tools`). Anything you sync between machines rides
  your own ssh trust, not any service of ours.
- **The skill installer** writes only under `~/.agents/skills/session-weaver`
  and creates/repoints symlinks named `session-weaver` inside each harness's
  skills directory. It refuses to delete a real file or directory it did not
  create (reported as a conflict instead), and `uninstall` removes only that
  skill's links/copies. `--dry-run` shows every action without touching disk.

The `agent-session-tools` dependency is pinned to an exact StudyLoop commit in
`pyproject.toml`, so `uv tool install` of a given Session Weaver commit is
reproducible and cannot silently pick up new upstream code.
