"""Install the session-weaver skill into the hub and wire harnesses to it.

Semantics (deliberately conservative):

- The skill's canonical copy lives in the hub (``~/.agents/skills/session-weaver``).
  Installing refreshes the hub copy (plain files, atomic per-file replace).
- Each selected harness that does NOT already read the hub gets a symlink
  ``<harness skills dir>/session-weaver -> <hub>/session-weaver``.
- Idempotent: a correct existing symlink is reported as unchanged; a wrong
  symlink is repointed. A REAL directory or file already occupying the target
  is never deleted — the action is reported as a conflict for the human.
- ``copy`` mode duplicates the skill directory into the harness's native
  skills directory instead of symlinking (for setups where symlinks are
  unwanted); hub-reader harnesses are still served by the hub.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .harnesses import Harness, hub_dir

SKILL_NAME = "session-weaver"


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


def install_skill(
    harnesses: list[Harness],
    *,
    skill_source: Path | None = None,
    home: Path | None = None,
    mode: str = "symlink",
    dry_run: bool = False,
) -> Report:
    """Install the skill into the hub and wire the selected harnesses to it."""
    if mode not in ("symlink", "copy"):
        raise ValueError("mode must be 'symlink' or 'copy'")
    source = skill_source or packaged_skill_dir()
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill source has no SKILL.md: {source}")

    report = Report()
    hub_skill = hub_dir(home) / SKILL_NAME
    _install_tree(source, hub_skill, dry_run=dry_run)
    report.add("hub-install", hub_skill, "canonical copy refreshed")

    for harness in harnesses:
        target = harness.resolved_skills_dir(home) / SKILL_NAME
        if harness.reads_hub and mode == "symlink":
            report.add("skip", target, f"{harness.name} already reads {hub_dir(home)}")
            continue
        if mode == "copy":
            if target.is_symlink():
                report.add("conflict", target, "symlink present; remove it before copy mode")
                continue
            _install_tree(source, target, dry_run=dry_run)
            report.add("copy", target, f"copied for {harness.name}")
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
    """Remove harness links (and optionally the hub copy). Copies are removed too."""
    report = Report()
    hub_skill = hub_dir(home) / SKILL_NAME
    for harness in harnesses:
        target = harness.resolved_skills_dir(home) / SKILL_NAME
        if target.is_symlink():
            if not dry_run:
                target.unlink()
            report.add("link", target, "symlink removed")
        elif target.is_dir():
            if not dry_run:
                shutil.rmtree(target)
            report.add("copy", target, "copied skill removed")
        else:
            report.add("skip", target, "nothing installed")
    if remove_hub and hub_skill.is_dir():
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
