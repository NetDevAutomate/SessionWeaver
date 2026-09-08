# Contributing to Session Weaver

Thank you for helping improve cross-harness session memory for coding agents.

## Getting started

```bash
git clone https://github.com/NetDevAutomate/SessionWeaver.git session_weaver
cd session_weaver
uv sync --frozen        # uses the reviewed dependency lock
uvx --from pre-commit==4.3.0 pre-commit install
uv run pytest -W error
uv run ruff check .
uv run ruff format --check .
uv run pyright
uvx --from pre-commit==4.3.0 pre-commit run --all-files
```

## What lives where

- `src/session_weaver/` + `tests/` — the maintained package: skill installer,
  doctor, concepts, wind-down, recall, ontology, projection, benchmarks and packaged
  `SKILL.md`. Behaviour changes here need tests (the pre-commit
  gate enforces ≥90% coverage) and green ruff/pyright.
- `SKILL.md` — the agent skill. It exists twice on purpose (repo root for
  GitHub, package data for what `session-weaver install` ships); edit one,
  copy to the other — `tests/test_skill_sync.py` fails if they drift.
- `docs/` — current user guides, claim-to-code audit and release instructions, alongside
  explicitly labelled historical results. Update current guides with behaviour changes.
- `code/`, historical results and `docs/architecture/poc/` — **frozen PoC evidence**.
  Preserve scripts and receipts; do not rewrite old measurements as current results.
  New measurements need separate evidence with the corpus and revision identified.
- `docs/architecture/` — Archify sources and retained exports. Keep a GitHub-renderable
  image in the [diagram guide](docs/architecture/README.md), with a text explanation.
- The session tools themselves (`session-export`, `session-query`, …) are
  developed in the [StudyLoop monorepo](https://github.com/NetDevAutomate/StudyLoop)
  (`packages/agent-session-tools`) — fix them THERE, then bump the pinned
  commit in this repo's `pyproject.toml` and re-lock.

## Ground rules

- Branch from `main` and open a pull request. The protected merge requires `gates`,
  `package`, `pre-commit` and `fixture-e2e` to pass; a direct push is not the release path.
- Tests first; changelog entry in the same change; pre-commit green before
  you push.
- Never include credentials, session transcripts, personal hostnames or
  unredacted local configuration in issues, fixtures, screenshots or logs —
  this project's whole subject matter is session transcripts, so this rule
  does the heavy lifting here.
- The installer must stay conservative: unowned targets are conflicts by default.
  Only explicit `install --copy --force` may replace the named harness target;
  uninstall requires the exact ownership marker or a link to the hub.
- Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1).
- Security concerns go through
  [GitHub's private advisory form](https://github.com/NetDevAutomate/SessionWeaver/security/advisories/new),
  never a public issue — see [SECURITY.md](SECURITY.md).

Session Weaver is a 0.2.x pre-release supporting six harnesses (Claude Code,
Codex, Kiro CLI, OpenCode, pi, Grok). Proposals for another harness start with
an issue describing where that harness discovers skills, not a pull request.

Read [OKF and knowledge](docs/knowledge.md), [ontology](docs/ontology.md) and the
[claims audit](docs/claims-audit.md) before changing their contracts. Follow
[RELEASING.md](docs/RELEASING.md) when cutting a version; a normal documentation push does
not create or move a release tag.

The public website lives in [StudyLoopSite](https://github.com/NetDevAutomate/StudyLoopSite)
(maintainer checkout: `/Users/ataylor/code/personal/sites/StudyLoopSite`). Keep its
SessionWeaver material aligned when behaviour changes; a repository push does not publish
that separate website.
