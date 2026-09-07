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

The tier-2 wind-down store authors knowledge concepts as OKF Markdown files
(frontmatter schema: type/title/description/tags/sources/verified/confidence/
actor). Session Weaver uses the format as a specification; no OKF code is
included.

## Contributor Covenant

**Source:** <https://www.contributor-covenant.org/version/2/1/code_of_conduct.html>
**Licence:** CC BY 4.0

`CODE_OF_CONDUCT.md` is the Contributor Covenant v2.1 with project-specific
contact details.

## skills-hub layout convention

The `~/.agents/skills` hub-and-symlink installation pattern follows the
convention established by StudyLoop's installers (MIT, same author) — recorded
here because this repo re-implements rather than imports it.
