"""Install the session-weaver skill into the hub and wire harnesses to it.

Semantics (deliberately conservative):

- The skill's canonical copy lives in the hub (``~/.agents/skills/session-weaver``).
  Installing refreshes the hub copy (plain files, atomic per-file replace).
- Each selected harness that does NOT already read the hub gets a symlink
  ``<harness skills dir>/session-weaver -> <hub>/session-weaver``.
- Idempotent: a correct existing symlink is reported as unchanged; a wrong
  symlink is repointed. By default, a real directory, file, or symlink already
  occupying a copy target is never deleted — it is a conflict. Explicit
  ``force`` in copy mode may replace only that named target.
- ``copy`` mode duplicates the skill directory into native directories for
  harnesses that do not read the hub. Each copy carries a deterministic
  ownership marker; uninstall removes it only when that marker is valid.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .harnesses import Harness, hub_dir

SKILL_NAME = "session-weaver"
OWNERSHIP_MARKER = ".session-weaver-owned.json"
OWNERSHIP_MARKER_BYTES = b'{"owner":"session-weaver","schema":1}\n'


def packaged_skill_dir() -> Path:
    """The skill directory shipped inside this package."""
    return Path(str(resources.files("session_weaver") / "data" / "skills" / SKILL_NAME))


@dataclass
class Action:
    """One thing the installer did (or would do, in dry-run)."""

    kind: str  # "hub-install" | "link" | "copy" | "skip" | "conflict"
    target: Path
    detail: str

    def __str__(self) -> str:
        return f"{self.kind:12} {self.target}  ({self.detail})"


@dataclass
class Report:
    actions: list[Action] = field(default_factory=list)

    def add(self, kind: str, target: Path, detail: str) -> None:
        self.actions.append(Action(kind, target, detail))

    @property
    def conflicts(self) -> list[Action]:
        return [a for a in self.actions if a.kind == "conflict"]


def _install_tree(source: Path, dest: Path, *, dry_run: bool) -> None:
    if dry_run:
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            tmp = target.with_name(target.name + ".swtmp")
            tmp.write_bytes(item.read_bytes())
            tmp.replace(target)


def _remove_install_target(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)


def _has_valid_ownership_marker(target: Path) -> bool:
    marker = target / OWNERSHIP_MARKER
    try:
        return not marker.is_symlink() and marker.read_bytes() == OWNERSHIP_MARKER_BYTES
    except OSError:
        return False


def _is_hub_link(target: Path, hub_skill: Path) -> bool:
    try:
        return target.resolve() == hub_skill.resolve()
    except (OSError, RuntimeError):
        return False


def install_skill(
    harnesses: list[Harness],
    *,
    skill_source: Path | None = None,
    home: Path | None = None,
    mode: str = "symlink",
    force: bool = False,
    dry_run: bool = False,
) -> Report:
    """Install the skill into the hub and wire the selected harnesses to it."""
    if mode not in ("symlink", "copy"):
        raise ValueError("mode must be 'symlink' or 'copy'")
    if force and mode != "copy":
        raise ValueError("force requires copy mode")
    source = skill_source or packaged_skill_dir()
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill source has no SKILL.md: {source}")

    report = Report()
    hub_skill = hub_dir(home) / SKILL_NAME
    hub_exists = hub_skill.is_symlink() or hub_skill.exists()
    if hub_exists and (
        hub_skill.is_symlink()
        or not hub_skill.is_dir()
        or not _has_valid_ownership_marker(hub_skill)
    ):
        report.add(
            "conflict",
            hub_skill,
            "canonical hub is unowned; valid SessionWeaver marker required before refresh",
        )
        return report
    _install_tree(source, hub_skill, dry_run=dry_run)
    if not dry_run:
        (hub_skill / OWNERSHIP_MARKER).write_bytes(OWNERSHIP_MARKER_BYTES)
    report.add("hub-install", hub_skill, "canonical copy refreshed")

    for harness in harnesses:
        target = harness.resolved_skills_dir(home) / SKILL_NAME
        if harness.reads_hub:
            report.add("skip", target, f"{harness.name} already reads {hub_dir(home)}")
            continue
        if mode == "copy":
            target_exists = target.is_symlink() or target.exists()
            if target_exists and not force:
                detail = (
                    "symlink present; remove it before copy mode or use --force"
                    if target.is_symlink()
                    else "target exists; use --force with copy mode to replace it"
                )
                report.add("conflict", target, detail)
                continue
            if target_exists and not dry_run:
                _remove_install_target(target)
            _install_tree(source, target, dry_run=dry_run)
            if not dry_run:
                (target / OWNERSHIP_MARKER).write_bytes(OWNERSHIP_MARKER_BYTES)
            detail = (
                f"replaced existing target for {harness.name}"
                if target_exists
                else f"copied for {harness.name}"
            )
            report.add("copy", target, detail)
            continue
        # symlink mode
        if target.is_symlink():
            if target.resolve() == hub_skill.resolve():
                report.add("skip", target, "already linked to the hub")
                continue
            if not dry_run:
                target.unlink()
                target.symlink_to(hub_skill)
            report.add("link", target, "repointed to the hub")
            continue
        if target.exists():
            report.add("conflict", target, "real file/directory in the way; not touching it")
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(hub_skill)
        report.add("link", target, f"linked for {harness.name}")

    return report


def uninstall_skill(
    harnesses: list[Harness],
    *,
    home: Path | None = None,
    remove_hub: bool = False,
    dry_run: bool = False,
) -> Report:
    """Remove recognized hub links and ownership-marked copies."""
    report = Report()
    hub_skill = hub_dir(home) / SKILL_NAME
    for harness in harnesses:
        target = harness.resolved_skills_dir(home) / SKILL_NAME
        if target.is_symlink():
            if not _is_hub_link(target, hub_skill):
                report.add(
                    "conflict",
                    target,
                    "symlink is unowned; expected a link to the SessionWeaver hub",
                )
                continue
            if not dry_run:
                target.unlink()
            report.add("link", target, "symlink removed")
        elif target.is_dir():
            if not _has_valid_ownership_marker(target):
                report.add(
                    "conflict",
                    target,
                    "directory is unowned; valid SessionWeaver marker required",
                )
                continue
            if not dry_run:
                shutil.rmtree(target)
            report.add("copy", target, "copied skill removed")
        elif target.exists():
            report.add("conflict", target, "plain file is unowned; not touching it")
        else:
            report.add("skip", target, "nothing installed")
    if remove_hub:
        hub_exists = hub_skill.is_symlink() or hub_skill.exists()
        if hub_exists and (
            hub_skill.is_symlink()
            or not hub_skill.is_dir()
            or not _has_valid_ownership_marker(hub_skill)
        ):
            report.add(
                "conflict",
                hub_skill,
                "canonical hub is unowned; valid SessionWeaver marker required for removal",
            )
        elif hub_exists:
            if not dry_run:
                shutil.rmtree(hub_skill)
            report.add("hub-install", hub_skill, "hub copy removed")
    return report


def status(harnesses: list[Harness], *, home: Path | None = None) -> Report:
    """Report the current installation state without changing anything."""
    report = Report()
    hub_skill = hub_dir(home) / SKILL_NAME
    if (hub_skill / "SKILL.md").is_file():
        report.add("hub-install", hub_skill, "present")
    else:
        report.add("skip", hub_skill, "hub copy missing")
    for harness in harnesses:
        target = harness.resolved_skills_dir(home) / SKILL_NAME
        if target.is_symlink():
            ok = target.resolve() == hub_skill.resolve()
            report.add("link", target, "-> hub" if ok else f"-> {target.resolve()} (NOT hub)")
        elif (target / "SKILL.md").is_file():
            report.add("copy", target, "independent copy")
        elif harness.reads_hub:
            report.add("skip", target, f"{harness.name} reads the hub directly")
        else:
            report.add("skip", target, "not installed")
    return report
