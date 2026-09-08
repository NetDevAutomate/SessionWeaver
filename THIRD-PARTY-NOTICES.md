# Third-Party Notices

Session Weaver is MIT licensed (see `LICENSE`). This file records third-party
work that influenced or is included in it, and the terms that work carries.

---

## agent-session-tools (StudyLoop monorepo)

**Source:** <https://github.com/NetDevAutomate/StudyLoop> (`packages/agent-session-tools`)
**Licence:** MIT — Copyright (c) 2026 Andy Taylor

First-party by authorship but a separate distribution: Session Weaver consumes
it as a pinned git dependency and re-exports its console scripts. Its own
transitive dependencies (rich, PyYAML, python-dotenv, typer, fastmcp) carry
their own licences, resolved from PyPI at install time.

## Open Knowledge Format (OKF)

**Source:** Google's Open Knowledge Format, v0.2 (June 2026)

The frozen PoC used Markdown with OKF-inspired metadata. The maintained importer accepts
that specific legacy writer shape, not arbitrary OKF documents. Current wind-down writes
authoritative SQLite concepts and `concept project` emits a SessionWeaver-specific Markdown
projection. See [the OKF guide](docs/knowledge.md) for compatibility and trust boundaries.
No OKF implementation code is included.

## Archify

**Source:** <https://github.com/tt-a1i/archify>
**Licence:** MIT

The architecture directory retains Archify specifications, rendered diagrams and validation
receipts. The documentation embeds its static image exports for GitHub readers.

## Contributor Covenant

**Source:** <https://www.contributor-covenant.org/version/2/1/code_of_conduct.html>
**Licence:** CC BY 4.0

`CODE_OF_CONDUCT.md` is the Contributor Covenant v2.1 with project-specific
contact details.

## skills-hub layout convention

The `~/.agents/skills` hub-and-symlink installation pattern follows the
convention established by StudyLoop's installers (MIT, same author) — recorded
here because this repo re-implements rather than imports it.
