# Public claims audit

This audit maps each externally testable claim in `README.md`, `SKILL.md`, and the published
Phase 2 diagrams to the maintained implementation, regression test, or retained evidence that
makes it true. Historical PoC values are explicitly labelled history and are not claims about
current behavior.

## Distribution, ownership, and command surface

| Public claim | Maintained evidence | Re-runnable check |
| --- | --- | --- |
| Session Weaver is a standalone uv-tool distribution and upstream session tools remain StudyLoop-owned at a pinned revision. | `pyproject.toml` project dependency and `[project.scripts]`; StudyLoop remains the package source. | `uv lock --check`; inspect `pyproject.toml`. |
| The install exposes `session-weaver` plus six `session-*` entry points. | `pyproject.toml:[project.scripts]`. | `uv run session-weaver --help`; inspect installed console scripts. |
| README lists every leaf command and no invented command. | `src/session_weaver/cli.py::build_parser`. | `tests/test_docs.py::test_readme_command_block_matches_build_parser`. |
| The canonical skill hub is `~/.agents/skills/session-weaver`; Codex/OpenCode/pi read it directly and Claude/Kiro/Grok use native paths. | `src/session_weaver/harnesses.py::HARNESSES`, `src/session_weaver/installer.py::install_skill`. | `tests/test_harnesses.py`; `tests/test_installer.py`. |
| Copy mode applies only to non-hub readers; replacement requires explicit `--copy --force` and is target-bounded. | `src/session_weaver/installer.py::install_skill`. | `tests/test_installer.py::test_hub_readers_are_also_skipped_in_copy_mode`; `test_copy_mode_force_replaces_only_the_named_target`. |
| Installer ownership bytes end in one LF: `b'{"owner":"session-weaver","schema":1}\n'`. | `src/session_weaver/installer.py` ownership marker constant/write path. | `tests/test_installer.py::test_install_writes_stable_hub_ownership_marker`; `tests/test_docs.py::test_readme_shows_the_ownership_marker_trailing_lf_exactly`. |
| Uninstall removes only exact owned directories or Session Weaver hub links and preserves unowned content. | `src/session_weaver/installer.py::uninstall_skill`. | Ownership and preservation cases in `tests/test_installer.py`. |
| Phase B capabilities are not claimed as landed. | README scope statement and the seed/MCP/replication caveats; no Phase B implementation is represented by this repository's command surface. | `tests/test_docs.py`; cold-read review against `src/session_weaver/cli.py::build_parser`. |

## Recall, trust, and wind-down

| Public claim | Maintained evidence | Re-runnable check |
| --- | --- | --- |
| Recall is concept-first and appends deduplicated session hits. | `src/session_weaver/recall.py::recall`. | `tests/test_recall.py`; `tests/test_recall_review_round1.py`. |
| Planner behavior is tokenize → stop/short-word removal → quoted AND → bounded OR fallback. | `src/session_weaver/recall.py::plan`, `src/session_weaver/recall.py::recall`. | Planner/fallback tests in `tests/test_recall.py`. |
| Recall applies scope/project/tombstone/lifecycle policy and shares concept authorization with projection. | `src/session_weaver/authorization.py::authorized_concepts`; `src/session_weaver/recall.py::recall`; projection caller in `src/session_weaver/projection.py`. | Scope/tombstone/retirement tests in `tests/test_recall.py`; shared-seam tests in `tests/test_recall_review_round1.py`. |
| Recall has no embedding or ontology input. | Imports and SQL paths in `src/session_weaver/recall.py::recall`; the frozen report contract. | `tests/test_recall.py` architecture/source checks and `docs/data/recall-contract.json`. |
| Legacy-unbound recall retains session-level provenance and has no fabricated citations. | `src/session_weaver/recall.py::_concept_hit`. | `tests/test_recall.py::test_recall_returns_legacy_unbound_concept_with_label_and_empty_citations`. |
| `model_authorship: model-proposed` and `citation_binding: machine-confirmed` are separate projection fields; unbound roots use `absent`. | `src/session_weaver/projection.py::_render`. | Projection frontmatter tests in `tests/test_projection_live.py` and `tests/test_concept_cli.py`. |
| Wind-down is code-enforced and authoritative Markdown must not be hand-written. | `src/session_weaver/concepts.py::ConceptService.winddown`; CLI route in `src/session_weaver/cli.py::_winddown`. | `tests/test_winddown.py`; wind-down CLI tests in `tests/test_concept_cli.py`. |
| Wind-down accepts exactly the documented top-level/concept fields, 0–8 concepts, 1–8 verbatim quotes per concept, bounded types/titles/tags/confidence, and optional complete locator triples. | `src/session_weaver/winddown.py::_parse_winddown`. | Boundary and exact-field tests in `tests/test_winddown.py`. |
| The entire wind-down batch validates and writes transactionally with field-level errors. | `src/session_weaver/concepts.py::ConceptService.winddown`. | Rollback and malformed-later-concept tests in `tests/test_concepts.py`; CLI error mapping in `tests/test_concept_cli.py`. |
| Projection is disposable, scope-authorized, hash/manifest-owned, symlink-refusing, and preserves unowned/changed files. | `src/session_weaver/concepts.py::ConceptService.project`; publisher/preflight logic in `src/session_weaver/projection.py`. | `tests/test_projection_live.py`; projection cases in `tests/test_concept_cli.py`. |
| Legacy OKF imports begin `legacy-unbound`; historical wording does not create acceptance; exact visible evidence may create a proposed bound successor; dry-run and repeated imports are stable. | `src/session_weaver/concepts.py::ConceptService.import_okf`; parser in `src/session_weaver/okf.py`. | `tests/test_okf.py`; import CLI cases in `tests/test_concept_cli.py`. |

## Doctor and diagnostics

| Public claim | Classification | Maintained evidence | Re-runnable check |
| --- | --- | --- | --- |
| Doctor opens the store read-only and reports store counts. | Fatal on missing/unreadable. | `src/session_weaver/cli.py::_doctor`. | `tests/test_doctor.py::test_doctor_reports_unreadable_store`; connection-close regression. |
| Doctor runs ontology health/freshness and treats unhealthy status as fatal. | Fatal. | `src/session_weaver/cli.py::_doctor`; `src/session_weaver/ontology.py::ontology_status`. | Ontology doctor red paths in `tests/test_doctor.py`; dimension tests in `tests/test_ontology.py`. |
| Doctor verifies exact concept sidecar identity and a full-tuple FTS consistency digest. | Fatal on absent/invalid/inconsistent. | `src/session_weaver/cli.py::_doctor_concept_sidecar`; `src/session_weaver/concept_schema.py::_fts_consistency` and `_inspect_fts_consistency`. | `tests/test_doctor.py::test_doctor_treats_inconsistent_concept_sidecar_as_fatal`; `tests/test_concept_schema.py::test_fts_consistency_digest_is_stable_for_duplicate_ids_in_any_insertion_order`. |
| Doctor recalls a term read from indexed content and requires at least one real result. | Fatal when a known-term call fails or returns zero; report-only when no searchable term exists. | `src/session_weaver/cli.py::_doctor_recall`; `src/session_weaver/recall.py::recall`. | Positive and empty-result controls in `tests/test_doctor.py`. |
| Doctor reads Claude, Kiro, and Codex MCP registration from the three documented HOME-relative files. | Report-only. | `src/session_weaver/cli.py::_doctor_mcp_registration`. | Registered and absent temp-HOME controls in `tests/test_doctor.py`. |
| Missing Grok skill is visible but nonfatal. | Report-only. | `src/session_weaver/cli.py::_doctor_grok_skill`. | `tests/test_doctor.py::test_doctor_treats_mcp_registration_and_missing_grok_skill_as_report_only`. |
| Missing required `session-*` executables remain fatal. | Fatal. | `src/session_weaver/cli.py::_doctor`. | `tests/test_doctor.py::test_doctor_reports_missing_tools_without_hiding_healthy_ontology`. |

## Ontology and sync boundary

| Public claim | Maintained evidence | Re-runnable check |
| --- | --- | --- |
| Full/incremental ontology rebuild is deterministic and status is strictly read-only across schema, version, coverage, freshness, source counts, integrity, and hash. | `src/session_weaver/ontology.py::rebuild_ontology`; refactored per-dimension helpers and `src/session_weaver/ontology.py::ontology_status`. | `tests/test_ontology.py`; read-only CLI tests in `tests/test_cli.py`. |
| The live harness uses SQLite Online Backup, checks source sentinels, and cleans temporary artifacts. | `src/session_weaver/ontology_live.py::run_live_copy_acceptance`. | `tests/test_ontology_live.py`. |
| Evidence v3 records `candidate_sessions == 0` for the retained incremental no-op. | `src/session_weaver/ontology_live.py::_build_evidence`; `docs/data/ontology-tier1-baseline.json`. | `tests/test_ontology_live.py::test_live_copy_acceptance_mutates_only_backup_and_returns_sanitized_evidence`; opt-in live command in README. |
| The v2→v3 count deltas are +135 sessions, +6,074 messages, +192 individuals, +709 relations, and +441 structural rows, with classes/properties unchanged. | Old evidence-v2 values retained in A5 handoff/progress plus current `docs/data/ontology-tier1-baseline.json`; extraction version remains `tier1-v2-canonical-messages`. | Compare the A5 base `1bb80fda:docs/data/ontology-tier1-baseline.json` with the current file. |
| Ontology is absent from normal/global delta sync lists, but whole-file seed can carry existing rows until Phase B B2. | Pinned upstream sync constants/tests plus the explicitly documented `_seed_remote_db` caveat. | Sync-exclusion regressions in `tests/test_cli.py`; inspect pinned upstream `agent_session_tools.sync`. |

## Measured and historical claims

| Public claim | Maintained evidence | Re-runnable check |
| --- | --- | --- |
| Corpus posture is `post-fix/eligible` on exporter pin `fb606468`. | `docs/data/bench-baseline-phase-a.json` provenance/posture fields. | `session-weaver bench run` on the approved prepared Online Backup. |
| Overall all-25 recall@5 is 0.600000 with Wilson 95% CI [0.407391, 0.765969]; MRR@5 is 0.463333 with CI [0.286163, 0.650272]. | `docs/data/bench-baseline-phase-a.json`. | Arithmetic/schema checks in `tests/test_bench.py`; approved A6 live report. |
| K/P/R recall is 0.818182 / 0.250000 / 0.666667 with the published CIs; all fixed floors pass. | `docs/data/bench-baseline-phase-a.json`. | Gate/category tests in `tests/test_bench.py`. |
| Verdict is INVESTIGATE, not target success. | Aggregate verdict/exit code 3 in `docs/data/bench-baseline-phase-a.json`. | `tests/test_bench.py` fixed band tests; exact benchmark CLI exits 3. |
| All 25 questions are visible; visible-subset metrics equal all-25 and are not directly comparable to the frozen PoC 22-ID set. | Aggregate eligibility and comparability identity in `docs/data/bench-baseline-phase-a.json`. | Exact-identity tests in `tests/test_bench.py`. |
| Same-visibility raw-text positive control is PASS at 0.480000 recall / 0.312000 MRR. | `docs/data/bench-baseline-phase-a.json`. | Positive-control tests in `tests/test_bench.py`. |
| Directional P is 0/40 and non-gating. | `docs/data/bench-baseline-phase-a.json` directional section. | Directional audit/fail-closed tests in `tests/test_bench.py`. |
| Tier-1 ontology rebuild was parity preparation only and never a recall input. | Benchmark orchestration and absence of ontology imports/queries in recall. | `tests/test_bench.py`; source inspection of `src/session_weaver/bench.py` and `src/session_weaver/recall.py`. |
| PoC 0.64/0.50 concept-only and 0.68/0.50 fusion values are history only. | `docs/RESULTS-final.md` historical PoC evidence; explicit historical labels in README/SKILL. | `tests/test_docs.py::test_public_docs_remove_superseded_behavior_claims`. |

## Target-state diagram claims and provenance

| Public claim | Maintained evidence | Re-runnable check |
| --- | --- | --- |
| Wind-down/import writes authoritative concept state transactionally to SQLite concepts + sidecar; `concept project` reads that state and emits disposable Markdown. | `src/session_weaver/concepts.py::ConceptService.winddown`, `ConceptService.import_okf`, and `ConceptService.project`; `src/session_weaver/projection.py::project_concepts`; all three `docs/architecture/phase2-*.*.json` typed specs. | `tests/test_docs.py::test_target_state_diagrams_preserve_authoritative_concept_direction`; concept and projection suites. |
| Recall reads authorized concepts/sidecar plus canonical sessions; Tier-1 ontology derives from canonical sessions and is not a recall input. | `src/session_weaver/recall.py::recall`; architecture and data-flow typed relationships in `docs/architecture/phase2-*`. | Diagram semantic regression in `tests/test_docs.py`; recall source checks in `tests/test_recall.py`. |
| Each current diagram source passes 9/9 showcase checks with zero errors/warnings, and each delivery receipt binds the frozen specification and generated HTML SHA-256 values. | `docs/architecture/phase2-*.validation.json` and `docs/architecture/phase2-*.delivery.json`. | Re-run Archify `validate ... --quality showcase --json` and `deliver ... --quality showcase --json`; recompute both hashes. |
| Each visual-check receipt is bound to the delivered artifact and records containment/readability at 1440×900, 1600×1000, 1920×1080, and 2048×1320. | `docs/architecture/phase2-*.visual-check.json` plus screenshot sidecars. | Re-run Archify `visual-check`; compare receipt artifact SHA-256 to the delivery receipt. |
| Automated delivery and browser receipts do not establish visual polish; independent perceptual review is a separate image-capable boundary. | Twelve retained light/dark endpoint screenshots and the A5 report's explicit perceptual-review record. | Inspect every retained screenshot independently; do not infer perceptual PASS from machine receipts. |
| Superseded PoC artifacts were relocated as byte-identical R100 history with provenance hashes retained, never reused as current acceptance evidence. | `docs/architecture/poc/README.md` and the 16 tracked files under `docs/architecture/poc/`. | `git diff --summary 1bb80fda..ff3dd90e`; recompute the listed pre/post SHA-256 values. |

## Freshness, safety, and development claims

| Public claim | Maintained evidence | Re-runnable check |
| --- | --- | --- |
| This installer does not provision export hooks/sweeps or MCP registration. | Installer operations only manage skill hub/targets; no hook/MCP mutation in `src/session_weaver/installer.py::install_skill`. | Installer temp-HOME tests; `tests/test_docs.py`. |
| WAL-mode live databases use SQLite Online Backup rather than `cp` in maintained live validation. | `src/session_weaver/ontology_live.py::_create_online_backup`; benchmark/projection live harnesses. | Live safety tests in `tests/test_ontology_live.py`, `tests/test_bench_live.py`, and `tests/test_projection_live.py`. |
| Cross-machine sync cannot promise propagated forgetting across native transcripts, peers, backups, and notes. | This is an explicit limitation, not a success claim; upstream sync only governs transferred database state. | Review upstream sync contract and retained source systems before making any stronger claim. |
| Root and packaged skills are byte-identical. | The two tracked `SKILL.md` files. | `tests/test_skill_sync.py`. |
| The suite contains 522 tests: 517 default-selected and 5 opt-in live. | Pytest collection after the final review fix wave. | `uv run pytest --collect-only -q --no-cov` reports `517/522 tests collected (5 deselected)`; `tests/test_docs.py::test_public_test_inventory_matches_actual_pytest_collection` derives and compares both public claims to collection output. |
| Package coverage floor is 90%. | `pyproject.toml:[tool.pytest.ini_options]`. | `uv run pytest -W error`. |


## A7 packaging and release boundaries

| Public claim | Maintained evidence | Re-runnable check |
| --- | --- | --- |
| The distribution pins `agent-session-tools` to exact public main SHA `0adeb6c8ef958e453abe5b66d0bca39fdad6c309`, descended from exporter fix `7f9a19ec03a592b3892ab60f2bcca316a1430d4a`. | `pyproject.toml`, `uv.lock`, and `docs/data/release-evidence-0.2.0.json`; StudyLoop CI run 34160855304 is green at the exact pin. | `git merge-base --is-ancestor 7f9a19ec03a592b3892ab60f2bcca316a1430d4a 0adeb6c8ef958e453abe5b66d0bca39fdad6c309`; `gh run view 34160855304 --repo NetDevAutomate/StudyLoop`. |
| Clean wheel and sdist installations independently prove all re-exported console scripts and installed `session-export` provenance. | `session_weaver.installed_smoke` and the CI `package` job retain artifact hashes, environment-local command/module paths, dependency SHA, and Session Weaver SHA. | `uv build`; run the CI package commands from `.github/workflows/ci.yml`. |
| The representative chain is install → export → ontology rebuild → winddown → recall → concept project → doctor; live evidence mutates only an Online Backup and retains aggregate sentinels. | `tests/test_workflow_e2e.py` and `docs/data/workflow-e2e-baseline.json`. | Run the documented fixture job or the opt-in live test with `SESSION_WEAVER_WORKFLOW_SOURCE`. |
| Release ordering is merge → exact-SHA green CI → annotated tag → GitHub release from CHANGELOG text. | `docs/RELEASING.md`, `session_weaver.release_guard`, and the tag-only CI `release-guard` job. | Run the release guard with exact-run jobs JSON; inspect the release evidence index before tagging. |
| Recall@5 is `0.600000`, every fixed category floor passed, overall remained below the `0.64` target, and the verdict is **INVESTIGATE**. | `docs/data/bench-baseline-phase-a.json`; README and both skill copies preserve this wording and the wide interval, legacy-unbound posture, 25/25 limitation, and generalized MRR-interval limitation. | `tests/test_docs.py`; `tests/test_release.py`. |
| The separate corpus-verified directional P set returned zero hits across 40 queries comprising five paraphrases for each of eight target-fact clusters; it is non-gating, and its Wilson 95% interval [0.000000, 0.087625] is a query-level calculation assuming independent queries, not a cluster-aware eight-target interval. | `docs/data/bench-baseline-phase-a.json`; README and both skill copies. | `tests/test_release.py::test_public_docs_preserve_council_wording_and_release_boundaries`. |
