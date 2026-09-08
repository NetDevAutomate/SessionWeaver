"""A7 release preparation, artifact provenance, and CI control contracts."""

from __future__ import annotations

import importlib
import importlib.util
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PIN = "0adeb6c8ef958e453abe5b66d0bca39fdad6c309"
FIX_SHA = "7f9a19ec03a592b3892ab60f2bcca316a1430d4a"
DIRECTIONAL_SENTENCE = (
    "The separate corpus-verified directional P set returned zero hits across 40 queries "
    "comprising five paraphrases for each of eight target-fact clusters; it is non-gating, "
    "and its Wilson 95% interval [0.000000, 0.087625] is a query-level calculation assuming "
    "independent queries, not a cluster-aware eight-target interval."
)
INVESTIGATE_SENTENCE = (
    "Recall@5 is `0.600000`, every fixed category floor passed, overall remained below the "
    "`0.64` target, and the verdict is **INVESTIGATE**."
)
REEXPORTED_COMMANDS = {
    "session-context",
    "session-export",
    "session-maint",
    "session-query",
    "session-repair",
    "session-sync",
    "session-weaver",
}


def test_release_metadata_uses_exact_post_fix_pin_and_020() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependency = next(
        item
        for item in pyproject["project"]["dependencies"]
        if item.startswith("agent-session-tools")
    )
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == "0.2.0"
    assert f"@{PIN}#subdirectory=packages/agent-session-tools" in dependency
    assert PIN in lock
    assert "c8315ccb74a7255446396fe616dddd684e1b0d14" not in lock
    unreleased_index = changelog.find("## [Unreleased]")
    released_index = changelog.find("## [0.2.0] - 2026-09-08")
    assert unreleased_index != -1
    assert released_index != -1
    assert unreleased_index < released_index
    assert (
        "[Unreleased]: https://github.com/NetDevAutomate/SessionWeaver/compare/v0.2.0...HEAD"
        in changelog
    )
    assert (
        "[0.2.0]: https://github.com/NetDevAutomate/SessionWeaver/compare/v0.1.0...v0.2.0"
        in changelog
    )


def test_version_is_loaded_from_installed_package_metadata() -> None:
    module_path = ROOT / "src" / "session_weaver" / "__init__.py"
    source = module_path.read_text(encoding="utf-8")

    assert "importlib.metadata" in source
    assert '__version__ = "0.2.0"' not in source


def test_installed_smoke_contract_checks_vcs_metadata_and_every_reexport(
    tmp_path: Path,
) -> None:
    spec = importlib.util.find_spec("session_weaver.installed_smoke")
    assert spec is not None, "installed artifact provenance verifier is missing"
    smoke = importlib.import_module("session_weaver.installed_smoke")

    assert smoke.PINNED_AGENT_SESSION_TOOLS_SHA == PIN
    assert set(smoke.REQUIRED_CONSOLE_SCRIPTS) == REEXPORTED_COMMANDS
    assert (
        smoke.extract_vcs_commit(
            {
                "url": "https://github.com/NetDevAutomate/StudyLoop.git",
                "vcs_info": {"vcs": "git", "commit_id": PIN, "requested_revision": PIN},
            }
        )
        == PIN
    )
    with pytest.raises(ValueError, match="direct_url"):
        smoke.extract_vcs_commit({"url": "https://example.invalid/archive.whl"})
    artifact = tmp_path / "session_weaver-0.2.0.whl"
    artifact.touch()
    assert smoke.extract_file_url({"url": artifact.as_uri()}) == artifact.resolve()
    with pytest.raises(ValueError, match="file URL"):
        smoke.extract_file_url({"url": "https://example.invalid/session-weaver.whl"})


def test_installed_smoke_missing_artifact_reports_sanitized_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    smoke = importlib.import_module("session_weaver.installed_smoke")
    missing_artifact = tmp_path / "missing.whl"

    result = smoke.main(
        [
            "--artifact",
            str(missing_artifact),
            "--kind",
            "wheel",
            "--session-weaver-sha",
            "test-sha",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err.startswith("installed artifact verification failed:")
    assert len(captured.err.splitlines()) == 1
    assert "Traceback" not in captured.err
    assert "installed_smoke.py" not in captured.err
    assert str(ROOT) not in captured.err


def _release_root(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "session-weaver"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [0.2.0] - 2026-09-08\n\nPrepared.\n\n"
        "## [0.1.0] - 2026-09-07\n\nPrevious.\n\n"
        "[Unreleased]: https://github.com/NetDevAutomate/SessionWeaver/compare/v0.2.0...HEAD\n"
        "[0.2.0]: https://github.com/NetDevAutomate/SessionWeaver/compare/v0.1.0...v0.2.0\n",
        encoding="utf-8",
    )
    return tmp_path


def test_release_guard_requires_tag_sha_green_sha_jobs_and_release_files(tmp_path: Path) -> None:
    spec = importlib.util.find_spec("session_weaver.release_guard")
    assert spec is not None, "release guard module is missing"
    guard = importlib.import_module("session_weaver.release_guard")
    root = _release_root(tmp_path)

    report = guard.validate_release(
        root,
        tag="v0.2.0",
        tag_sha="abc123",
        green_sha="abc123",
        jobs={"gates": "success", "package": "success"},
    )
    assert report == {
        "version": "0.2.0",
        "tag": "v0.2.0",
        "sha": "abc123",
        "required_jobs": ["gates", "package"],
    }

    with pytest.raises(guard.ReleaseGuardError, match="tag SHA"):
        guard.validate_release(
            root,
            tag="v0.2.0",
            tag_sha="abc123",
            green_sha="different",
            jobs={"gates": "success", "package": "success"},
        )
    with pytest.raises(guard.ReleaseGuardError, match="package"):
        guard.validate_release(
            root,
            tag="v0.2.0",
            tag_sha="abc123",
            green_sha="abc123",
            jobs={"gates": "success", "package": "failure"},
        )

    changelog = root / "CHANGELOG.md"
    changelog.write_text(
        changelog.read_text(encoding="utf-8").replace(
            "compare/v0.1.0...v0.2.0",
            "compare/v9.9.9...v0.2.0",
        ),
        encoding="utf-8",
    )
    with pytest.raises(guard.ReleaseGuardError, match="compare link"):
        guard.validate_release(
            root,
            tag="v0.2.0",
            tag_sha="abc123",
            green_sha="abc123",
            jobs={"gates": "success", "package": "success"},
        )


def test_release_guard_cli_reads_jobs_receipt_and_emits_verified_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    guard = importlib.import_module("session_weaver.release_guard")
    root = _release_root(tmp_path)
    jobs = root / "jobs.json"
    jobs.write_text(
        json.dumps(
            {
                "jobs": [
                    {"name": "gates", "conclusion": "success"},
                    {"name": "package", "conclusion": "success"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = guard.main(
        [
            "--repo-root",
            str(root),
            "--tag",
            "v0.2.0",
            "--tag-sha",
            "abc123",
            "--green-sha",
            "abc123",
            "--jobs-json",
            str(jobs),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "required_jobs": ["gates", "package"],
        "sha": "abc123",
        "tag": "v0.2.0",
        "version": "0.2.0",
    }


def _ci_workflow() -> dict[Any, Any]:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    assert isinstance(workflow, dict)
    return workflow


def _named_steps(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in job["steps"] if "name" in step}


def _normalized_run(step: dict[str, Any]) -> str:
    return " ".join(step["run"].split())


def test_ci_job_graph_and_dependencies_are_structurally_pinned() -> None:
    jobs = _ci_workflow()["jobs"]

    assert set(jobs) == {
        "quality",
        "gates",
        "package",
        "pre-commit",
        "fixture-e2e",
        "release-guard",
    }
    assert jobs["quality"]["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest"],
        "python": ["3.12", "3.13"],
    }
    assert jobs["gates"]["needs"] == "quality"
    assert jobs["release-guard"]["needs"] == [
        "gates",
        "package",
        "pre-commit",
        "fixture-e2e",
    ]
    for independent_job in ("quality", "package", "pre-commit", "fixture-e2e"):
        assert "needs" not in jobs[independent_job]


def test_ci_precommit_uses_exact_event_sha_and_pinned_runner() -> None:
    expression = (
        "${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.head.sha || github.sha }}"
    )
    precommit = _ci_workflow()["jobs"]["pre-commit"]
    checkout = next(
        step for step in precommit["steps"] if step.get("uses") == "actions/checkout@v4"
    )
    run_step = _named_steps(precommit)["Run pinned pre-commit suite"]

    assert checkout["with"]["ref"] == expression
    assert run_step["env"]["CHECK_COMMIT_SHA"] == expression
    assert run_step["run"] == "uvx --from pre-commit==4.3.0 pre-commit run --all-files"


def test_ci_package_job_structurally_pins_dual_artifact_provenance() -> None:
    package = _ci_workflow()["jobs"]["package"]
    checkout = next(step for step in package["steps"] if step.get("uses") == "actions/checkout@v4")
    steps = _named_steps(package)

    assert checkout["with"]["fetch-depth"] == 0
    assert steps["Build wheel and sdist"]["run"] == "uv build"
    wheel = _normalized_run(steps["Clean wheel install and provenance smoke"])
    sdist = _normalized_run(steps["Independent clean sdist install and provenance smoke"])
    for command, kind, receipt in (
        (wheel, "wheel", "wheel-install-receipt.json"),
        (sdist, "sdist", "sdist-install-receipt.json"),
    ):
        assert "uv venv --python 3.12" in command
        assert "uv pip install --python" in command
        assert "-I -m session_weaver.installed_smoke" in command
        assert f"--kind {kind}" in command
        assert '--session-weaver-sha "$GITHUB_SHA"' in command
        assert f"--receipt {receipt}" in command

    upload = next(
        step for step in package["steps"] if step.get("uses") == "actions/upload-artifact@v4"
    )
    assert upload["with"]["name"] == "session-weaver-package-${{ github.sha }}"
    assert set(upload["with"]["path"].splitlines()) == {
        "dist/*",
        "wheel-install-receipt.json",
        "sdist-install-receipt.json",
    }


def test_ci_fixture_e2e_command_is_structurally_pinned() -> None:
    fixture = _ci_workflow()["jobs"]["fixture-e2e"]
    run_step = _named_steps(fixture)["Run representative fixture workflow"]

    assert _normalized_run(run_step) == (
        'uv run pytest tests/test_workflow_e2e.py -m "not live" --no-cov -W error'
    )


def test_ci_release_guard_is_tag_only_and_uses_exact_run_jobs() -> None:
    workflow = _ci_workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None
    guard = workflow["jobs"]["release-guard"]
    checkout = next(step for step in guard["steps"] if step.get("uses") == "actions/checkout@v4")
    steps = _named_steps(guard)

    assert triggers["push"]["tags"] == ["v*"]
    assert guard["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert guard["needs"] == ["gates", "package", "pre-commit", "fixture-e2e"]
    assert checkout["with"]["fetch-depth"] == 0
    assert steps["Fetch exact-run job conclusions"]["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert steps["Fetch exact-run job conclusions"]["run"] == (
        'gh api --paginate "repos/${GITHUB_REPOSITORY}/actions/runs/'
        '${GITHUB_RUN_ID}/jobs" > "${RUNNER_TEMP}/jobs.json"'
    )
    assert _normalized_run(steps["Prove tag SHA equals the green package/gates SHA"]) == (
        "uv run python -m session_weaver.release_guard "
        '--tag "$GITHUB_REF_NAME" --green-sha "$GITHUB_SHA" '
        '--jobs-json "${RUNNER_TEMP}/jobs.json"'
    )


def test_release_sequence_completes_evidence_after_real_guard_before_release() -> None:
    releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")

    tag_push = releasing.index("git push origin vX.Y.Z")
    real_guard = releasing.index("Wait for the real tag workflow")
    evidence = releasing.index("Complete `docs/data/release-evidence-0.2.0.json`")
    github_release = releasing.index("gh release create vX.Y.Z")

    assert tag_push < real_guard < evidence < github_release
    assert "post-tag evidence commit" in releasing
    assert "not part of the tagged source tree" in " ".join(releasing.split())


def test_ci_standards_description_matches_a7_job_boundaries() -> None:
    path = ROOT / ".ci-standards.yaml"
    text = path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)

    assert set(config["checks"]) == {"lint-format", "lint", "typecheck", "test"}
    for job in (
        "quality",
        "gates",
        "package",
        "pre-commit",
        "fixture-e2e",
        "release-guard",
    ):
        assert job in text
    for boundary in (
        "runs only the host-side quality commands",
        "`ci-standards run-job` is required",
        "PR-head checkout",
        "upload-artifact",
        "local act",
    ):
        assert boundary in text


def test_public_docs_preserve_council_wording_and_release_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
    combined = readme + skill + releasing

    for text in (readme, skill):
        assert INVESTIGATE_SENTENCE in text
        assert DIRECTIONAL_SENTENCE in text
        assert "legacy-unbound" in text
        assert "whole-file seed" in text
        assert "diagnostic" in text
        assert "Phase B" in text
    for phrase in (
        "default branch",
        "reviewed local checkout",
        "explicitly pinned revision",
        "post-merge",
        "user-managed",
        "report-only",
        "uv tool install --force",
        "source-tree tests do not prove installed artifacts",
    ):
        assert phrase in combined
    assert "MCP registration" in combined
    assert "liveness" in combined
    assert "propagated forgetting" in combined
    assert "embeddings" in combined
    assert "ontology-backed recall" in combined
    assert "target-band quality" in combined
    assert "required_status_checks[contexts][]=gates" in releasing
    assert "required_status_checks[contexts][]=package" in releasing
    assert "required_status_checks[contexts][]=pre-commit" in releasing


def test_workflow_baseline_is_aggregate_only() -> None:
    baseline = json.loads(
        (ROOT / "docs" / "data" / "workflow-e2e-baseline.json").read_text(encoding="utf-8")
    )

    assert baseline["schema"] == "sessionweaver-workflow-e2e-v1"
    assert baseline["source_sentinels_unchanged"] is True
    assert baseline["workflow"] == [
        "install",
        "export",
        "ontology_rebuild",
        "winddown",
        "recall",
        "concept_project",
        "doctor",
    ]
    assert set(baseline) == {
        "schema",
        "tested_session_weaver_sha",
        "source_sentinels_unchanged",
        "workflow",
        "aggregates",
    }
    assert all(isinstance(value, int) for value in baseline["aggregates"].values())
