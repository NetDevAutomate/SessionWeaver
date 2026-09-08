<p align="center">
  <img src="images/sessionweaver-icon.png" alt="Session Weaver" width="160" />
</p>

# Session Weaver

Session Weaver is a standalone distribution of cross-harness session capture, evidence-backed
concept recall, wind-down writing, ontology diagnostics, and benchmark tooling. One `uv tool`
install exposes the `session-*` tools plus the `session-weaver` CLI and agent skill.

The maintained session-tool implementation and database schema remain owned by the
[StudyLoop monorepo](https://github.com/NetDevAutomate/StudyLoop) package
`packages/agent-session-tools`; this repository consumes that source at a pinned commit rather
than forking it. The standalone installer does not provision user-managed `session-export` freshness automation,
MCP registration, or MCP liveness. Doctor reports registration as a report-only diagnostic; it
is not installed-package certification or server-liveness proof. Phase B's StudyLoop retrofit,
seed-snapshot sanitization, cross-machine concept replication or convergence, propagated
forgetting, embeddings, ontology-backed recall, and target-band quality have not landed and are
not claimed by `0.2.0`.

## Install

An unqualified pre-merge Git install follows the repository default branch; it does **not**
install an unmerged review branch. During review, use either a reviewed local checkout or an
explicitly pinned revision:

```bash
# Reviewed local checkout
uv tool install --from /path/to/session_weaver session-weaver
# Explicitly pinned revision
uv tool install \
  --from git+https://github.com/NetDevAutomate/SessionWeaver@<reviewed-sha> \
  session-weaver
```

After merge, a post-merge unqualified install follows the updated default branch. A released
operator install should prefer the immutable tag:

```bash
uv tool install \
  --from git+https://github.com/NetDevAutomate/SessionWeaver@v0.2.0 \
  session-weaver
```

The install exposes `session-weaver`, `session-export`, `session-query`, `session-context`,
`session-sync`, `session-repair`, and `session-maint`. If another `agent-session-tools` uv tool
already owns the same `session-*` executable names, choose which distribution owns those names;
`uv tool install --force` replaces the existing tool's executable links. Source-tree tests do not
prove installed artifacts: release evidence independently installs and verifies both the wheel
and sdist in clean environments.

### Wire the skill into your harnesses

The following block contains every leaf command from `session_weaver.cli:build_parser`:

```bash
session-weaver install [--harness all|NAMES] [--copy] [--force] [--dry-run]
session-weaver uninstall [--harness all|NAMES] [--remove-hub] [--dry-run]
session-weaver status [--harness all|NAMES]
session-weaver doctor [--db PATH]
session-weaver ontology rebuild [--incremental] [--db PATH]
session-weaver ontology status [--db PATH]
session-weaver winddown --session ID --from winddown.json [--project ID] [--db PATH]
session-weaver concept accept CONCEPT_ID --reason REASON [--project ID] [--db PATH]
session-weaver concept retire CONCEPT_ID --reason REASON [--project ID] [--db PATH]
session-weaver concept bind LEGACY_ID --from binding.json --reason REASON [--project ID] [--db PATH]
session-weaver concept import-okf /path/to/okf [--dry-run] [--report PATH|-] [--project ID] [--db PATH]
session-weaver concept project --out DIR [--project ID] [--db PATH] [--json]
session-weaver recall "<question>" [--k N] [--project ID] [--db PATH] [--json]
session-weaver bench run --db PATH [--gold PATH] [--k 5] [--json] [--out DIR] [--live-ro]
session-weaver bench audit-gold --db PATH
```

`install` writes the canonical skill to `~/.agents/skills/session-weaver/`. Codex, OpenCode, and
pi read that hub directly. Claude, Kiro, and Grok receive a link from their native skill
directory; `--copy` creates copies only for those non-hub readers. Existing named targets are
non-destructive conflicts unless `--copy --force` explicitly replaces that one target.
Installer-owned directories contain the exact marker payload
`b'{"owner":"session-weaver","schema":1}\n'`; the shown `\n` is the trailing LF byte. Uninstall
removes only a directory carrying that exact marker or a link resolving to the Session Weaver
hub.

## Recall and trust

`session-weaver recall` performs concept-first keyword retrieval, then appends deduplicated raw
session hits. Its planner tokenizes terms, drops the pinned stop/short-word set, quotes every term,
tries an AND query, and uses OR only when needed to fill `k`. Concepts and sessions pass through
the maintained scope, project, tombstone, and lifecycle policy. The report shape is frozen in
[`docs/data/recall-contract.json`](docs/data/recall-contract.json).

Recall ships with no embeddings and never reads tier-1 ontology tables. Projection and recall use
the same `authorization.authorized_concepts` seam, so both apply one concept-visibility policy.
Returned legacy records remain labelled `legacy-unbound (session-level provenance)` with no
fabricated citations.

Projection frontmatter separates two facts:

- `model_authorship: model-proposed` — a model authored the interpretation; this is not human
  approval.
- `citation_binding: machine-confirmed` — exact quoted text is bound to visible evidence. It is
  `absent` for legacy-unbound roots.

## Code-enforced wind-down

Use `session-weaver winddown --session ID --from winddown.json`; never write authoritative OKF
Markdown by hand. The input contains only `concepts` (0–8). Each concept has exactly `type`,
`title`, `description`, `tags`, `confidence`, and `quotes`; every concept has 1–8 verbatim quotes.
A quote may be text-only or include the complete `evidence_id` / `start` / `end` locator triple.
The writer validates the complete batch, resolves quotes against scope-visible evidence, assigns
identities, and commits authoritative state transactionally. Field-level errors produce no
partial concept batch.

`concept project --out DIR` rebuilds disposable Markdown from authoritative concept state. Its
scope/project marker and manifest bind generated filenames to concept IDs and exact byte hashes.
Managed stale files are removed only when the prior manifest, generated-name grammar, embedded
ownership marker, current hash, and regular-file check all agree. Unowned, changed, or symlinked
files are preserved as conflicts.

Legacy `concept import-okf` remains a conservative migration path. Imported roots begin as
`legacy-unbound`; exact evidence can create a proposed bound successor, but historical
`machine-confirmed` wording never becomes acceptance. Dry-run follows the same classification
without writes, and repeated import is idempotent.

## Doctor classifications

`session-weaver doctor` opens the selected store read-only and runs functional positive controls:
store counts, ontology freshness/health, exact concept-sidecar schema plus full-tuple FTS digest,
and recall of a term read from indexed content with at least one returned result. It also reads
MCP registration from `~/.claude.json`, `~/.kiro/settings/mcp.json`, and
`~/.codex/config.toml`, and checks the Grok skill path.

| Classification | Findings | Exit effect |
| --- | --- | --- |
| Fatal | missing/unreadable session store; unhealthy ontology; missing/invalid concept sidecar; inconsistent FTS digest; recall positive-control failure; missing required `session-*` executables | exit 1 |
| Report-only | Claude/Kiro/Codex session-db MCP registration missing or unreadable; Grok skill absent; no searchable term in an otherwise empty store | no exit change |

MCP checks are visibility diagnostics, not proof that a remote server is running. The standalone
installer does not register MCP servers.

## Ontology boundary

`ontology rebuild` materializes deterministic tier-1 derived tables; `ontology status` is
strictly read-only and reports schema, version, coverage, freshness, source-count, orphan,
foreign-key, domain/range, and logical-hash dimensions. The live acceptance harness mutates only
a SQLite Online Backup and compares source sentinels before deleting the backup.

Ontology tables are absent from pinned normal/global delta-sync allow lists. The pinned
first-time whole-file seed (`_seed_remote_db`) transfers a SQLite Online Backup and can therefore
carry existing derived rows. Phase B B2 owns seed sanitization and destination-local rebuild;
until it lands, "excluded from delta sync" must not be paraphrased as "never travels by any sync
path."

The fresh A5 evidence-v3 baseline is
[`docs/data/ontology-tier1-baseline.json`](docs/data/ontology-tier1-baseline.json). It includes
`incremental_rebuild.candidate_sessions == 0` so the retained no-op claim is directly evidenced.
Compared with evidence v2, the fresh source grew from 5,678 to 5,813 sessions (+135) and 133,559
to 139,633 messages (+6,074). Derived counts changed from 13,336 to 13,528 individuals (+192),
28,766 to 29,475 relations (+709), and 22,800 to 23,241 structural rows (+441); 7 classes and 6
properties were unchanged. These are corpus-growth deltas, not a change to extraction version
`tier1-v2-canonical-messages`.

## A6 measured posture

The A6 corpus posture was **post-fix/eligible** on exporter `fb606468`. At k=5, all 25 frozen
questions were visibility-eligible, so all-25 and visible-subset results are identical:

| Category | Recall@5 | Wilson 95% CI | MRR@5 | Wilson 95% CI |
| --- | ---: | --- | ---: | --- |
| Overall | **0.600000** | **[0.407391, 0.765969]** | **0.463333** | **[0.286163, 0.650272]** |
| K | 0.818182 | [0.523014, 0.948633] | 0.621212 | [0.341058, 0.838617] |
| P | 0.250000 | [0.071478, 0.590730] | 0.156250 | [0.032809, 0.502727] |
| R | 0.666667 | [0.299988, 0.903231] | 0.583333 | [0.241074, 0.860536] |

Recall@5 is `0.600000`, every fixed category floor passed, overall remained below the `0.64` target, and the verdict is **INVESTIGATE**. The overall Recall@5 interval remains wide at
`[0.407391, 0.765969]`. Current **25/25** visibility is **not directly comparable** to the frozen
PoC's 22 IDs, and the evaluated concept corpus consisted of 2,033 imported legacy-unbound roots;
those are recall-visible historical signal, not accepted or exact-cited assertions. The published
MRR intervals are generalized score intervals over reciprocal-rank values, not exact binomial
confidence intervals. The same-visibility raw-text positive control was **PASS** at 0.480000
recall / 0.312000 MRR.

The separate corpus-verified directional P set returned zero hits across 40 queries comprising five paraphrases for each of eight target-fact clusters; it is non-gating, and its Wilson 95% interval [0.000000, 0.087625] is a query-level calculation assuming independent queries, not a cluster-aware eight-target interval.
This is the retained **0/40** directional result.

Tier-1 ontology rebuild was parity preparation only and was never a recall input.

Historical PoC values are retained only as history: concept-only 0.64/0.50 and unshipped fusion
0.68/0.50. They are not the current gate result and do not describe a shipped embedding or fusion
path. The approved aggregate A6 evidence is
[`docs/data/bench-baseline-phase-a.json`](docs/data/bench-baseline-phase-a.json).

## Freshness and scope limits

Session export freshness comes from user-managed `session-export` hooks or sweep configuration;
this installer provisions neither automation nor a fixed schedule. Check the local environment
instead of assuming recent sessions were exported.

Cross-machine sync transfers retained database content through the upstream session tools. It
does not guarantee propagated forgetting: native transcripts, peers, backups, and exported notes
can retain or restore data.

## Development

```bash
uv sync --frozen
uv run pytest -W error
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

The current suite contains **503 tests**: 498 selected by the default non-live run and 5 opt-in
live tests. Package coverage is required to remain at least 90%. The ontology live test requires
an explicit source and writes retained evidence only when an explicit target is supplied:

```bash
SESSION_WEAVER_ONTOLOGY_SOURCE=/absolute/path/to/sessions.db \
SESSION_WEAVER_ONTOLOGY_EVIDENCE=docs/data/ontology-tier1-baseline.json \
  uv run pytest tests/test_ontology_live.py::test_real_corpus_online_backup_acceptance \
  -m live --no-cov -W error
```

Testable public claims are mapped to maintained code/tests/evidence in
[`docs/claims-audit.md`](docs/claims-audit.md).

The current target-state diagrams are [`phase2-architecture`](docs/architecture/phase2-architecture.html),
[`phase2-dataflow`](docs/architecture/phase2-dataflow.html), and
[`phase2-winddown-sequence`](docs/architecture/phase2-winddown-sequence.html). The original PoC
artifacts and their unchanged provenance sidecars are retained under
[`docs/architecture/poc/`](docs/architecture/poc/README.md) and are superseded by `phase2-*`.

## License and project files

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
[SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md), and
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Session Weaver is MIT licensed; see
[LICENSE](LICENSE).
