"""Fail closed unless a release tag matches green CI and prepared release files."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

_REQUIRED_JOBS = ("gates", "package")


class ReleaseGuardError(ValueError):
    """Release metadata or exact-SHA evidence is incomplete or inconsistent."""


def _project_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseGuardError("pyproject.toml has no valid release version")
    return version


def validate_release(
    root: Path,
    *,
    tag: str,
    tag_sha: str,
    green_sha: str,
    jobs: dict[str, str | None],
) -> dict[str, Any]:
    """Validate exact tag/SHA evidence, job conclusions, and prepared release files."""
    version = _project_version(root)
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ReleaseGuardError(f"tag {tag!r} does not match pyproject version {version}")
    if tag_sha != green_sha:
        raise ReleaseGuardError(f"tag SHA {tag_sha} does not equal green SHA {green_sha}")
    for job in _REQUIRED_JOBS:
        if jobs.get(job) != "success":
            raise ReleaseGuardError(f"required job {job} is not successful at {green_sha}")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$"
    if re.search(heading, changelog, flags=re.MULTILINE) is None:
        raise ReleaseGuardError(f"CHANGELOG.md has no dated {version} release heading")
    expected_unreleased = (
        f"[Unreleased]: https://github.com/NetDevAutomate/SessionWeaver/compare/v{version}...HEAD"
    )
    if expected_unreleased not in changelog:
        raise ReleaseGuardError("CHANGELOG.md Unreleased compare link is stale")
    release_headings = re.findall(
        r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$",
        changelog,
        flags=re.MULTILINE,
    )
    try:
        current_index = release_headings.index(version)
        previous_version = release_headings[current_index + 1]
    except (ValueError, IndexError) as exc:
        raise ReleaseGuardError(
            f"CHANGELOG.md has no previous dated release below {version}"
        ) from exc
    expected_release = (
        f"[{version}]: https://github.com/NetDevAutomate/SessionWeaver/"
        f"compare/v{previous_version}...v{version}"
    )
    if expected_release not in changelog:
        raise ReleaseGuardError(f"CHANGELOG.md compare link for {version} is stale")

    return {
        "version": version,
        "tag": tag,
        "sha": green_sha,
        "required_jobs": list(_REQUIRED_JOBS),
    }


def _jobs(path: Path) -> dict[str, str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ReleaseGuardError("jobs JSON has no jobs array")
    result: dict[str, str | None] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            conclusion = row.get("conclusion")
            result[row["name"]] = conclusion if isinstance(conclusion, str) else None
    return result


def _tag_sha(root: Path, tag: str) -> str:
    completed = subprocess.run(
        ["git", "rev-list", "-n", "1", f"refs/tags/{tag}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME"))
    parser.add_argument("--tag-sha", help="explicit local dry-run tag target; CI resolves the tag")
    parser.add_argument("--green-sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--jobs-json", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.tag or not args.green_sha:
        parser.error("--tag and --green-sha are required outside GitHub Actions")
    root = args.repo_root.resolve()
    try:
        report = validate_release(
            root,
            tag=args.tag,
            tag_sha=args.tag_sha or _tag_sha(root, args.tag),
            green_sha=args.green_sha,
            jobs=_jobs(args.jobs_json),
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        tomllib.TOMLDecodeError,
    ) as exc:
        print(f"release guard failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
