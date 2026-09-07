# Contributing to Session Weaver

Thank you for helping improve cross-harness session memory for coding agents.

## Getting started

```bash
git clone https://github.com/NetDevAutomate/SessionWeaver.git session_weaver
cd session_weaver
uv sync                # resolves agent-session-tools from the pinned StudyLoop commit
uv run pre-commit install
uv run pytest --cov=session_weaver
uv run ruff check . && uv run ruff format --check .
```

## What lives where

- `src/session_weaver/` + `tests/` — the maintained package: skill installer,
  doctor CLI, packaged `SKILL.md`. Changes here need tests (the pre-commit
  gate enforces ≥90% coverage) and green ruff/pyright.
- `SKILL.md` — the agent skill. It exists twice on purpose (repo root for
  GitHub, package data for what `session-weaver install` ships); edit one,
  copy to the other — `tests/test_skill_sync.py` fails if they drift.
- `code/` and `docs/` — **frozen PoC evidence.** The scripts and measurements
  that justified the architecture. Corrections to prose are welcome; do not
  rewrite results. New measurements go in a new addendum in
  `docs/RESULTS-final.md` (later measurements supersede earlier ones — see the
  reading-order note there).
- The session tools themselves (`session-export`, `session-query`, …) are
  developed in the [StudyLoop monorepo](https://github.com/NetDevAutomate/StudyLoop)
  (`packages/agent-session-tools`) — fix them THERE, then bump the pinned
  commit in this repo's `pyproject.toml` and re-lock.

## Ground rules

- Branch from `main` with a `feat/`, `fix/`, `docs/` or `test/` prefix.
- Tests first; changelog entry in the same change; pre-commit green before
  you push.
- Never include credentials, session transcripts, personal hostnames or
  unredacted local configuration in issues, fixtures, screenshots or logs —
  this project's whole subject matter is session transcripts, so this rule
  does the heavy lifting here.
- The installer must stay conservative: it never deletes a real file or
  directory it did not create. Treat that as an invariant, with tests.
- Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1).
- Security concerns go through
  [GitHub's private advisory form](https://github.com/NetDevAutomate/SessionWeaver/security/advisories/new),
  never a public issue — see [SECURITY.md](SECURITY.md).

Session Weaver is a 0.1.x pre-release supporting six harnesses (Claude Code,
Codex, Kiro CLI, OpenCode, pi, Grok). Proposals for another harness start with
an issue describing where that harness discovers skills, not a pull request.
