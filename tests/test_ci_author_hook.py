"""Regression contracts for CI and local commit-author verification."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
EXPECTED_NAME = "NetDevAutomate"
EXPECTED_EMAIL = "andy.taylor@mail.com"
SELECTED_SHA_EXPRESSION = (
    "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}"
)
IDENTITY_ENV_KEYS = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)
CONFIG_ENV_KEYS = (
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM",
)
CONFIG_ENV_PREFIXES = (
    "GIT_CONFIG_KEY_",
    "GIT_CONFIG_VALUE_",
)
OPERATION_ENV_KEYS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_NAMESPACE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _base_env(repo: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key in (
            *IDENTITY_ENV_KEYS,
            *CONFIG_ENV_KEYS,
            *OPERATION_ENV_KEYS,
            "ALLOW_GITHUB_MERGE_COMMIT",
            "CHECK_COMMIT_SHA",
        ) or key.startswith(CONFIG_ENV_PREFIXES):
            env.pop(key)
    env.update(
        {
            "HOME": str(repo.parent / "configless-home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    return env


def _prospective_config_env(repo: Path) -> dict[str, str]:
    return {
        **_base_env(repo),
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "user.name",
        "GIT_CONFIG_VALUE_0": EXPECTED_NAME,
        "GIT_CONFIG_KEY_1": "user.email",
        "GIT_CONFIG_VALUE_1": EXPECTED_EMAIL,
    }


def _commit(
    repo: Path,
    *,
    author_name: str = EXPECTED_NAME,
    author_email: str = EXPECTED_EMAIL,
    committer_name: str = EXPECTED_NAME,
    committer_email: str = EXPECTED_EMAIL,
) -> str:
    repo.mkdir()
    isolated_env = _base_env(repo)
    _git(repo, "init", "--quiet", env=isolated_env)
    (repo / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", "fixture.txt", env=isolated_env)
    env = {
        **_base_env(repo),
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": committer_name,
        "GIT_COMMITTER_EMAIL": committer_email,
    }
    _git(repo, "commit", "--quiet", "-m", "fixture", env=env)
    return _git(repo, "rev-parse", "HEAD", env=_base_env(repo))


def _author_hook_command() -> list[str]:
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    hook = next(
        hook
        for repository in config["repos"]
        if repository["repo"] == "local"
        for hook in repository["hooks"]
        if hook["id"] == "check-commit-author"
    )
    return shlex.split(hook["entry"])


def _run_hook(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _author_hook_command(),
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_ci_mode_accepts_valid_commit_without_git_identity_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit_sha = _commit(repo)

    result = _run_hook(repo, {**_base_env(repo), "CHECK_COMMIT_SHA": commit_sha})

    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_mode_uses_commit_metadata_not_prospective_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit_sha = _commit(
        repo,
        author_name="Wrong Author",
        author_email="wrong-author@example.invalid",
        committer_name="Wrong Committer",
        committer_email="wrong-committer@example.invalid",
    )

    result = _run_hook(
        repo,
        {**_prospective_config_env(repo), "CHECK_COMMIT_SHA": commit_sha},
    )

    assert result.returncode == 1
    assert "Wrong Author <wrong-author@example.invalid>" in result.stdout
    assert "Wrong Committer <wrong-committer@example.invalid>" in result.stdout


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("author_name", "Wrong Author"),
        ("author_email", "wrong-author@example.invalid"),
        ("committer_name", "Wrong Committer"),
        ("committer_email", "wrong-committer@example.invalid"),
    ],
)
def test_ci_mode_rejects_each_commit_identity_field_mismatch(
    tmp_path: Path,
    field: str,
    wrong_value: str,
) -> None:
    repo = tmp_path / "repo"
    identity = {
        "author_name": EXPECTED_NAME,
        "author_email": EXPECTED_EMAIL,
        "committer_name": EXPECTED_NAME,
        "committer_email": EXPECTED_EMAIL,
    }
    identity[field] = wrong_value
    commit_sha = _commit(repo, **identity)

    result = _run_hook(repo, {**_base_env(repo), "CHECK_COMMIT_SHA": commit_sha})

    assert result.returncode == 1
    assert "Commits must be authored by" in result.stdout


@pytest.mark.parametrize("commit_sha", ["", "not-a-commit"])
def test_ci_mode_fails_closed_for_missing_or_invalid_commit(
    tmp_path: Path,
    commit_sha: str,
) -> None:
    repo = tmp_path / "repo"
    _commit(repo)

    result = _run_hook(repo, {**_base_env(repo), "CHECK_COMMIT_SHA": commit_sha})

    assert result.returncode == 1
    assert "CHECK_COMMIT_SHA must identify a commit" in result.stdout


def test_local_mode_preserves_prospective_git_environment_and_config_enforcement(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _commit(repo)
    config_env = _prospective_config_env(repo)

    config_result = _run_hook(repo, config_env)
    override_result = _run_hook(
        repo,
        {
            **config_env,
            "GIT_AUTHOR_NAME": "Wrong Author",
            "GIT_AUTHOR_EMAIL": EXPECTED_EMAIL,
            "GIT_COMMITTER_NAME": EXPECTED_NAME,
            "GIT_COMMITTER_EMAIL": EXPECTED_EMAIL,
        },
    )

    assert config_result.returncode == 0, config_result.stdout + config_result.stderr
    assert override_result.returncode == 1
    assert "Wrong Author" in override_result.stdout


def test_fixture_env_scrubs_ambient_git_config_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    ambient_config = tmp_path / "ambient.gitconfig"
    ambient_config.write_text(
        "[user]\n\tname = Wrong Config File\n\temail = wrong-file@example.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG", str(ambient_config))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(ambient_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")
    monkeypatch.setenv(
        "GIT_CONFIG_PARAMETERS",
        "'user.name'='Wrong Ambient' 'user.email'='wrong-ambient@example.invalid'",
    )
    monkeypatch.setenv("GIT_CONFIG_KEY_7", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_7", "Wrong Indexed Ambient")

    _commit(repo)
    fixture_env = _prospective_config_env(repo)
    result = _run_hook(repo, fixture_env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture_env["HOME"] == str(repo.parent / "configless-home")
    assert fixture_env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert fixture_env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert fixture_env["GIT_CONFIG_KEY_0"] == "user.name"
    assert fixture_env["GIT_CONFIG_VALUE_0"] == EXPECTED_NAME
    assert fixture_env["GIT_CONFIG_KEY_1"] == "user.email"
    assert fixture_env["GIT_CONFIG_VALUE_1"] == EXPECTED_EMAIL
    assert "GIT_CONFIG" not in fixture_env
    assert "GIT_CONFIG_PARAMETERS" not in fixture_env
    assert "GIT_CONFIG_KEY_7" not in fixture_env
    assert "GIT_CONFIG_VALUE_7" not in fixture_env


def test_precommit_workflow_checks_out_and_verifies_the_same_selected_sha() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["pre-commit"]["steps"]
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    run_step = next(step for step in steps if step.get("name") == "Run pinned pre-commit suite")

    assert checkout["with"]["ref"] == SELECTED_SHA_EXPRESSION
    assert run_step["env"]["CHECK_COMMIT_SHA"] == SELECTED_SHA_EXPRESSION
    assert run_step["run"] == ("uvx --from pre-commit==4.3.0 pre-commit run --all-files")


GITHUB_MERGE_COMMITTER_NAME = "GitHub"
GITHUB_MERGE_COMMITTER_EMAIL = "noreply@github.com"
MERGE_MODE_EXPRESSION = (
    "${{ (github.ref == 'refs/heads/main' || "
    "startsWith(github.ref, 'refs/tags/v')) && 'true' || 'false' }}"
)
RELEASING_DOC = ROOT / "docs" / "RELEASING.md"


def _github_merge_upstream(
    tmp_path: Path,
    *,
    merge_committer_name: str = GITHUB_MERGE_COMMITTER_NAME,
    merge_committer_email: str = GITHUB_MERGE_COMMITTER_EMAIL,
    feature_author_name: str = EXPECTED_NAME,
    feature_author_email: str = EXPECTED_EMAIL,
    feature_committer_name: str = EXPECTED_NAME,
    feature_committer_email: str = EXPECTED_EMAIL,
    merge_target: str = "main",
    feature_branches: int = 1,
) -> tuple[Path, str]:
    """Build an upstream repo whose ``merge_target`` tip is a GitHub-shaped merge."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    base_env = _base_env(upstream)
    expected_identity = {
        "GIT_AUTHOR_NAME": EXPECTED_NAME,
        "GIT_AUTHOR_EMAIL": EXPECTED_EMAIL,
        "GIT_COMMITTER_NAME": EXPECTED_NAME,
        "GIT_COMMITTER_EMAIL": EXPECTED_EMAIL,
    }
    _git(upstream, "init", "--quiet", "--initial-branch=main", env=base_env)
    (upstream / "base.txt").write_text("base\n", encoding="utf-8")
    _git(upstream, "add", "base.txt", env=base_env)
    _git(upstream, "commit", "--quiet", "-m", "base", env={**base_env, **expected_identity})
    if merge_target != "main":
        _git(upstream, "checkout", "--quiet", "-b", merge_target, env=base_env)
    feature_identity = {
        "GIT_AUTHOR_NAME": feature_author_name,
        "GIT_AUTHOR_EMAIL": feature_author_email,
        "GIT_COMMITTER_NAME": feature_committer_name,
        "GIT_COMMITTER_EMAIL": feature_committer_email,
    }
    branch_names: list[str] = []
    for index in range(feature_branches):
        branch = f"feature-{index}"
        _git(upstream, "checkout", "--quiet", "-b", branch, merge_target, env=base_env)
        (upstream / f"{branch}.txt").write_text(f"{branch}\n", encoding="utf-8")
        _git(upstream, "add", f"{branch}.txt", env=base_env)
        _git(
            upstream,
            "commit",
            "--quiet",
            "-m",
            branch,
            env={**base_env, **feature_identity},
        )
        branch_names.append(branch)
    _git(upstream, "checkout", "--quiet", merge_target, env=base_env)
    merge_identity = {
        # GitHub records the merging account as author; the hook must not check it.
        "GIT_AUTHOR_NAME": "Andy Taylor",
        "GIT_AUTHOR_EMAIL": EXPECTED_EMAIL,
        "GIT_COMMITTER_NAME": merge_committer_name,
        "GIT_COMMITTER_EMAIL": merge_committer_email,
    }
    _git(
        upstream,
        "merge",
        "--quiet",
        "--no-ff",
        "--no-edit",
        *branch_names,
        env={**base_env, **merge_identity},
    )
    merge_sha = _git(upstream, "rev-parse", merge_target, env=base_env)
    return upstream, merge_sha


def _github_merge_clone(tmp_path: Path, **kwargs: object) -> tuple[Path, str]:
    """Clone the GitHub-shaped upstream so ``origin/main`` exists as a remote ref."""
    upstream, merge_sha = _github_merge_upstream(tmp_path, **kwargs)  # type: ignore[arg-type]
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "--quiet", str(upstream), str(clone), env=_base_env(upstream))
    return clone, merge_sha


def _merge_mode_env(repo: Path, commit_sha: str) -> dict[str, str]:
    return {
        **_base_env(repo),
        "CHECK_COMMIT_SHA": commit_sha,
        "ALLOW_GITHUB_MERGE_COMMIT": "true",
    }


def test_merge_mode_accepts_github_shaped_merge_on_origin_main(tmp_path: Path) -> None:
    clone, merge_sha = _github_merge_clone(tmp_path)

    result = _run_hook(clone, _merge_mode_env(clone, merge_sha))

    assert result.returncode == 0, result.stdout + result.stderr


def test_merge_mode_does_not_check_merge_author_identity(tmp_path: Path) -> None:
    clone, merge_sha = _github_merge_clone(tmp_path)
    merge_author = _git(clone, "show", "-s", "--format=%an", merge_sha, env=_base_env(clone))

    result = _run_hook(clone, _merge_mode_env(clone, merge_sha))

    assert merge_author != EXPECTED_NAME
    assert result.returncode == 0, result.stdout + result.stderr


def test_merge_mode_rejects_wrong_merge_committer(tmp_path: Path) -> None:
    clone, merge_sha = _github_merge_clone(
        tmp_path,
        merge_committer_name="Wrong Bot",
        merge_committer_email="wrong-bot@example.invalid",
    )

    result = _run_hook(clone, _merge_mode_env(clone, merge_sha))

    assert result.returncode == 1
    assert "merge committer must be GitHub <noreply@github.com>" in result.stdout


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("feature_author_name", "Wrong Author"),
        ("feature_author_email", "wrong-author@example.invalid"),
        ("feature_committer_name", "Wrong Committer"),
        ("feature_committer_email", "wrong-committer@example.invalid"),
    ],
)
def test_merge_mode_rejects_each_second_parent_identity_mismatch(
    tmp_path: Path,
    field: str,
    wrong_value: str,
) -> None:
    clone, merge_sha = _github_merge_clone(tmp_path, **{field: wrong_value})

    result = _run_hook(clone, _merge_mode_env(clone, merge_sha))

    assert result.returncode == 1
    assert "merged PR head must be authored by" in result.stdout


def test_merge_mode_rejects_one_parent_commit_even_with_valid_identity(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    commit_sha = _commit(repo)

    result = _run_hook(repo, _merge_mode_env(repo, commit_sha))

    assert result.returncode == 1
    assert "two-parent merge commit" in result.stdout


def test_merge_mode_rejects_octopus_merge(tmp_path: Path) -> None:
    clone, merge_sha = _github_merge_clone(tmp_path, feature_branches=2)

    result = _run_hook(clone, _merge_mode_env(clone, merge_sha))

    assert result.returncode == 1
    assert "two-parent merge commit" in result.stdout


def test_merge_mode_accepts_v_tag_pointing_at_on_main_merge(tmp_path: Path) -> None:
    clone, merge_sha = _github_merge_clone(tmp_path)
    tag_env = {
        **_base_env(clone),
        "GIT_AUTHOR_NAME": EXPECTED_NAME,
        "GIT_AUTHOR_EMAIL": EXPECTED_EMAIL,
        "GIT_COMMITTER_NAME": EXPECTED_NAME,
        "GIT_COMMITTER_EMAIL": EXPECTED_EMAIL,
    }
    _git(clone, "tag", "-a", "v1.2.3", merge_sha, "-m", "release", env=tag_env)

    result = _run_hook(clone, _merge_mode_env(clone, "v1.2.3"))

    assert result.returncode == 0, result.stdout + result.stderr


def test_merge_mode_rejects_github_shaped_merge_not_on_origin_main(
    tmp_path: Path,
) -> None:
    clone, merge_sha = _github_merge_clone(tmp_path, merge_target="develop")

    result = _run_hook(clone, _merge_mode_env(clone, merge_sha))

    assert result.returncode == 1
    assert "origin/main first-parent history" in result.stdout


def test_merge_mode_fails_closed_without_fetched_origin_main(tmp_path: Path) -> None:
    upstream, merge_sha = _github_merge_upstream(tmp_path)

    result = _run_hook(upstream, _merge_mode_env(upstream, merge_sha))

    assert result.returncode == 1
    assert "requires fetched origin/main" in result.stdout


@pytest.mark.parametrize("commit_sha", ["", "not-a-commit"])
def test_merge_mode_fails_closed_for_missing_or_invalid_commit(
    tmp_path: Path,
    commit_sha: str,
) -> None:
    clone, _ = _github_merge_clone(tmp_path)

    result = _run_hook(clone, _merge_mode_env(clone, commit_sha))

    assert result.returncode == 1
    assert "CHECK_COMMIT_SHA must identify a commit" in result.stdout


@pytest.mark.parametrize("flag_env", [{}, {"ALLOW_GITHUB_MERGE_COMMIT": "false"}])
def test_strict_ci_mode_still_rejects_github_merge_without_trusted_flag(
    tmp_path: Path,
    flag_env: dict[str, str],
) -> None:
    clone, merge_sha = _github_merge_clone(tmp_path)

    result = _run_hook(
        clone,
        {**_base_env(clone), "CHECK_COMMIT_SHA": merge_sha, **flag_env},
    )

    assert result.returncode == 1
    assert "Commits must be authored by" in result.stdout


def test_local_prospective_mode_ignores_merge_flag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _commit(repo)
    config_env = {**_prospective_config_env(repo), "ALLOW_GITHUB_MERGE_COMMIT": "true"}

    valid_result = _run_hook(repo, config_env)
    invalid_result = _run_hook(
        repo,
        {**config_env, "GIT_AUTHOR_NAME": "Wrong Author"},
    )

    assert valid_result.returncode == 0, valid_result.stdout + valid_result.stderr
    assert invalid_result.returncode == 1
    assert "Wrong Author" in invalid_result.stdout


def test_precommit_workflow_wires_merge_mode_flag_and_full_fetch() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["pre-commit"]["steps"]
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    run_step = next(step for step in steps if step.get("name") == "Run pinned pre-commit suite")

    assert checkout["with"]["ref"] == SELECTED_SHA_EXPRESSION
    assert checkout["with"]["fetch-depth"] == 0
    assert run_step["env"]["CHECK_COMMIT_SHA"] == SELECTED_SHA_EXPRESSION
    assert run_step["env"]["ALLOW_GITHUB_MERGE_COMMIT"] == MERGE_MODE_EXPRESSION


def test_releasing_docs_require_v_tag_ruleset_and_state_trust_boundary() -> None:
    releasing = RELEASING_DOC.read_text(encoding="utf-8")

    assert "Protect `v*` tags before release" in releasing
    assert "Server-merge trust boundary" in releasing
    assert "GitHub <noreply@github.com>" in releasing
    assert "first-parent" in releasing
    assert releasing.index("Protect `v*` tags before release") < releasing.index(
        "## Release sequence"
    )
