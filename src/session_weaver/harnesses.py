"""The coding-agent harnesses Session Weaver knows how to wire a skill into.

The layout follows the hub pattern StudyLoop's installers established: a skill
is installed ONCE into ``~/.agents/skills/`` (the hub), and each harness either
reads that directory natively or gets a symlink from its own skills directory
into the hub. Two links in a chain rather than N parallel copies, because
parallel copies drift.

Hub readers (no per-harness link needed): Codex reads ``~/.agents/skills`` as
its user scope, OpenCode lists it as a global search path, and pi discovers it
directly. Kiro, Claude and Grok read their own native directories, so they get
a symlink.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def hub_dir(home: Path | None = None) -> Path:
    """The canonical shared skills hub: ``~/.agents/skills``."""
    return (home or Path.home()) / ".agents" / "skills"


@dataclass(frozen=True)
class Harness:
    """One coding-agent harness and how it discovers skills.

    ``skills_dir`` is the harness's native skills directory relative to home.
    ``reads_hub`` means the harness already searches the hub directly, so a
    symlink from its native directory would only create a duplicate discovery
    path — the installer skips it (and says so) unless the skill is installed
    with ``copy`` mode to an explicit directory.
    """

    name: str
    skills_dir: str
    reads_hub: bool = False

    def resolved_skills_dir(self, home: Path | None = None) -> Path:
        return (home or Path.home()) / self.skills_dir


HARNESSES: dict[str, Harness] = {
    h.name: h
    for h in (
        Harness("claude", ".claude/skills"),
        Harness("kiro", ".kiro/skills"),
        Harness("grok", ".grok/skills"),
        Harness("codex", ".codex/skills", reads_hub=True),
        Harness("opencode", ".config/opencode/skills", reads_hub=True),
        Harness("pi", ".pi/agent/skills", reads_hub=True),
    )
}


def parse_harness_selection(value: str) -> list[Harness]:
    """Parse ``--harness`` values: ``all`` or a comma-separated name list."""
    if value.strip().lower() == "all":
        return list(HARNESSES.values())
    chosen: list[Harness] = []
    for raw in value.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name not in HARNESSES:
            known = ", ".join(sorted(HARNESSES))
            raise ValueError(f"unknown harness {name!r}; known: {known} (or 'all')")
        chosen.append(HARNESSES[name])
    if not chosen:
        raise ValueError("no harness selected")
    return chosen
