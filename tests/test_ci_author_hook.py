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
