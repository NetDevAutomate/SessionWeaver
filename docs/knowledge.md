# OKF and reusable knowledge

[Overview](../README.md) · [Ontology](ontology.md) · [Architecture](architecture/README.md)

A transcript records what was said. A concept records a decision, finding, problem,
preference or procedure worth carrying into the next session, with a route back to its source.
Use it to recover why a design was chosen or which constraint caused an earlier attempt to fail.

## What OKF means in this repository

Open Knowledge Format (OKF) influenced the original Markdown knowledge store: readable files
with YAML metadata for concepts and sources. In the maintained implementation, **SQLite is
authoritative**. Markdown is a disposable, inspectable projection of authorized concept state.
This preserves the usefulness of readable knowledge files while enforcing citations and
lifecycle changes in code.

There are three distinct contracts:

| Operation | Input or output | Role |
| --- | --- | --- |
| `winddown` | Strict JSON containing concepts and exact quotes | Create source-bound proposals transactionally. |
| `concept project` | Markdown with SessionWeaver frontmatter | Read or share a regenerable view of authorized concepts. |
| `concept import-okf` | The frozen legacy writer's specific Markdown/YAML shape | Migrate historical knowledge conservatively. |

The projection is **not** a round-trip input for the legacy importer, and the importer is not
a general OKF validator. Its accepted fields and bounds live in
[`okf.py`](../src/session_weaver/okf.py); projection fields live in
[`projection.py`](../src/session_weaver/projection.py).

## Record a useful concept

First export the session with `session-export` and configure the upstream context scope.
Scope is an explicit local policy: `memory.default_scope` and, where needed,
`memory.projects` in the selected session-tools configuration. Review changes with
`session-context policy plan` before `session-context policy apply`. A `--project` selector
does not grant access to otherwise hidden evidence.

Prepare `winddown.json` using the [complete JSON example in the skill](../SKILL.md#write-path--code-enforced-wind-down).
Replace its synthetic quote with verbatim text from the selected, scope-visible session.
Then run:

```bash
session-weaver winddown --session YOUR_SESSION_ID --from winddown.json
session-weaver recall "bounded online backups" --k 5 --json
session-weaver concept project --out /absolute/path/to/private/knowledge --json
```

The input has only `concepts`, containing zero to eight records. Each has exactly `type`,
`title`, `description`, `tags`, `confidence` and `quotes`. Each record requires one to eight
exact quotes; a quote may also supply the complete `evidence_id`, `start`, `end` locator.
The writer validates the entire batch before committing. A source mismatch is a reason to
inspect the evidence, not to invent a citation or edit the database directly.

Never hand-write authoritative OKF files. The projection can be regenerated; editing its
Markdown does not update the authoritative concept. Modified or unowned output files are
preserved as conflicts when projection runs again.

## Read the trust labels separately

| Label | What it establishes |
| --- | --- |
| `model_authorship: model-proposed` | The interpretation is model-authored, not human approval. |
| `citation_binding: machine-confirmed` | Exact quoted text is bound to visible source evidence. |
| `binding_state: legacy-unbound` | Historical knowledge has session-level provenance without exact citation closure. |
| `standing` | The concept's lifecycle state, such as proposed, accepted or retired. |

An exact quote can still support a mistaken or outdated interpretation. Review the source and
applicability before accepting a bound proposal:

```bash
session-weaver concept accept CONCEPT_ID --reason "Checked the cited source and current applicability"
session-weaver concept retire CONCEPT_ID --reason "Superseded by the current implementation"
```

Legacy roots cannot be accepted as though they already had exact citations. Use `concept bind`
to create a bound successor with real evidence first. Import historical files with a dry run:

```bash
session-weaver concept import-okf /absolute/path/to/legacy-okf --dry-run --report -
```

Omit `--dry-run` only when ready to persist the reported migration. Historical
`verified: {status: machine-confirmed, ...}` metadata does not confer acceptance.

## How this relates to the ontology

The [ontology](ontology.md) connects sessions through projects, artifacts, commands and test
summaries. Concepts explain meaning and rationale. The current recall command searches
concepts first and then deduplicated session text; it does **not** query ontology tables or
use embeddings. Its retained benchmark verdict is **INVESTIGATE**, described in the
[current measurements](../README.md#a6-measured-posture).

StudyLoop is the related learning application. SessionWeaver's concepts can preserve context
for a learner, but the planned StudyLoop retrofit is not shipped by this release and a concept
is not proof of mastery.
