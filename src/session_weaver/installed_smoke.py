"""Prove wheel/sdist installs resolve the pinned upstream tools in a clean environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
import sysconfig
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

PINNED_AGENT_SESSION_TOOLS_SHA = "0adeb6c8ef958e453abe5b66d0bca39fdad6c309"
_EXPECTED_AGENT_SESSION_TOOLS_URL = "https://github.com/NetDevAutomate/StudyLoop.git"
REQUIRED_CONSOLE_SCRIPTS = (
    "session-weaver",
    "session-context",
    "session-export",
    "session-maint",
    "session-query",
    "session-repair",
    "session-sync",
)
_EXPECTED_ENTRY_POINTS = {
    "session-weaver": "session_weaver.cli:main",
    "session-context": "agent_session_tools.context.cli:main",
    "session-export": "agent_session_tools.export_sessions:main",
    "session-maint": "agent_session_tools.maintenance:main",
    "session-query": "agent_session_tools.query_sessions:main",
    "session-repair": "agent_session_tools.repair:main",
    "session-sync": "agent_session_tools.sync:main",
}


def extract_vcs_commit(direct_url: dict[str, Any]) -> str:
    """Return a full git commit from PEP 610 direct-url metadata."""
    vcs_info = direct_url.get("vcs_info")
    if not isinstance(vcs_info, dict) or vcs_info.get("vcs") != "git":
        raise ValueError("agent-session-tools direct_url metadata is not a git checkout")
    commit = vcs_info.get("commit_id")
    if not isinstance(commit, str) or len(commit) != 40:
        raise ValueError("agent-session-tools direct_url metadata has no full commit_id")
    return commit


def extract_file_url(direct_url: dict[str, Any]) -> Path:
    """Resolve the installed distribution's PEP 610 file URL."""
    url = direct_url.get("url")
    if not isinstance(url, str):
        raise ValueError("session-weaver direct_url metadata has no URL")
    parsed = urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("session-weaver direct_url metadata is not a local file URL")
    return Path(unquote(parsed.path)).resolve()


def _distribution_direct_url(
    distribution: metadata.Distribution,
    *,
    package: str,
) -> dict[str, Any]:
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        raise ValueError(f"{package} distribution has no direct_url.json")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{package} direct_url.json is not an object")
    return parsed


def _entry_points(distribution: metadata.Distribution) -> dict[str, str]:
    return {
        entry.name: entry.value
        for entry in distribution.entry_points
        if entry.group == "console_scripts"
    }


def _inside_prefix(path: Path) -> bool:
    return path.resolve().is_relative_to(Path(sys.prefix).resolve())


def _run_help(command_paths: dict[str, Path]) -> None:
    commands = [
        [str(command_paths["session-weaver"]), "--version"],
        [str(command_paths["session-weaver"]), "doctor", "--help"],
    ]
    commands.extend([[str(path), "--help"] for path in command_paths.values()])
    for command in commands:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            name = Path(command[0]).name
            raise ValueError(f"{name} help smoke exited {completed.returncode}")


def verify_installed_artifact(
    artifact: Path,
    *,
    artifact_kind: str,
    session_weaver_sha: str,
) -> dict[str, Any]:
    """Verify one clean installation and return its machine-readable receipt."""
    if artifact_kind not in {"wheel", "sdist"}:
        raise ValueError("artifact kind must be wheel or sdist")
    artifact = artifact.resolve(strict=True)
    session_distribution = metadata.distribution("session-weaver")
    tools_distribution = metadata.distribution("agent-session-tools")
    session_direct_url = _distribution_direct_url(
        session_distribution,
        package="session-weaver",
    )
    installed_artifact = extract_file_url(session_direct_url)
    if installed_artifact != artifact:
        raise ValueError(
            f"installed session-weaver source {installed_artifact} does not match {artifact}"
        )
    direct_url = _distribution_direct_url(
        tools_distribution,
        package="agent-session-tools",
    )
    if direct_url.get("url") != _EXPECTED_AGENT_SESSION_TOOLS_URL:
        raise ValueError("installed agent-session-tools direct_url uses an unexpected repository")
    dependency_sha = extract_vcs_commit(direct_url)
    if dependency_sha != PINNED_AGENT_SESSION_TOOLS_SHA:
        raise ValueError(
            "installed agent-session-tools commit mismatch: "
            f"expected {PINNED_AGENT_SESSION_TOOLS_SHA}, got {dependency_sha}"
        )

    session_entries = _entry_points(session_distribution)
    for name, target in _EXPECTED_ENTRY_POINTS.items():
        if session_entries.get(name) != target:
            raise ValueError(f"session-weaver distribution entry point mismatch for {name}")

    scripts = Path(sysconfig.get_path("scripts"))
    command_paths = {name: scripts / name for name in REQUIRED_CONSOLE_SCRIPTS}
    for name, path in command_paths.items():
        if not path.is_file() or not _inside_prefix(path):
            raise ValueError(f"{name} does not resolve inside the clean environment")

    export_launcher = command_paths["session-export"].read_text(encoding="utf-8")
    if "agent_session_tools.export_sessions import main" not in export_launcher:
        raise ValueError("installed session-export launcher does not target the fixed wrapper")

    session_module = importlib.import_module("session_weaver")
    tools_module = importlib.import_module("agent_session_tools")
    session_module_path = Path(session_module.__file__ or "")
    tools_module_path = Path(tools_module.__file__ or "")
    if not _inside_prefix(session_module_path) or not _inside_prefix(tools_module_path):
        raise ValueError("package import escaped the clean installation environment")

    _run_help(command_paths)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    return {
        "schema": "sessionweaver-installed-artifact-v1",
        "artifact_kind": artifact_kind,
        "artifact_name": artifact.name,
        "artifact_sha256": digest,
        "installed_from": str(installed_artifact),
        "session_weaver_version": session_distribution.version,
        "session_weaver_sha": session_weaver_sha,
        "agent_session_tools_sha": dependency_sha,
        "agent_session_tools_url": direct_url.get("url"),
        "session_export_entry_point": _EXPECTED_ENTRY_POINTS["session-export"],
        "session_export_launcher": str(command_paths["session-export"].resolve()),
        "module_provenance": {
            "session_weaver": str(session_module_path.resolve()),
            "agent_session_tools": str(tools_module_path.resolve()),
        },
        "commands": {name: str(path.resolve()) for name, path in command_paths.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=("wheel", "sdist"))
    parser.add_argument("--session-weaver-sha", required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = verify_installed_artifact(
            args.artifact,
            artifact_kind=args.kind,
            session_weaver_sha=args.session_weaver_sha,
        )
    except (OSError, ValueError, metadata.PackageNotFoundError, json.JSONDecodeError) as exc:
        print(f"installed artifact verification failed: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt is not None:
        args.receipt.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
