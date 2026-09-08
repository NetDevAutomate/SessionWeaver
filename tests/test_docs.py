"""A5 public claims stay congruent with the maintained CLI and measured evidence."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from session_weaver.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SKILL = ROOT / "SKILL.md"
CLAIMS_AUDIT = ROOT / "docs" / "claims-audit.md"

EXPECTED_COMMANDS = {
    "install",
    "uninstall",
    "status",
    "doctor",
    "ontology rebuild",
    "ontology status",
    "winddown",
    "concept accept",
    "concept retire",
    "concept bind",
    "concept import-okf",
    "concept project",
    "recall",
    "bench run",
    "bench audit-gold",
}

MEASURED_CLAIMS = (
    "0.600000",
    "[0.407391, 0.765969]",
    "0.463333",
    "[0.286163, 0.650272]",
    "0.818182",
    "[0.523014, 0.948633]",
    "0.250000",
    "[0.071478, 0.590730]",
    "0.666667",
    "[0.299988, 0.903231]",
    "INVESTIGATE",
    "25/25",
    "not directly comparable",
    "positive control",
    "PASS",
    "0/40",
    "non-gating",
)


def _leaf_commands(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> set[str]:
    commands: set[str] = set()
    subparsers = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    if not subparsers:
        return {" ".join(prefix)}
    for name, child in subparsers[0].choices.items():
        commands.update(_leaf_commands(child, (*prefix, name)))
    return commands


def _documented_commands(readme: str) -> set[str]:
    block = readme.split("### Wire the skill into your harnesses", 1)[1].split("```", 2)[1]
    commands: set[str] = set()
    for line in block.splitlines():
        match = re.match(r"session-weaver\s+(\S+)(?:\s+(\S+))?", line)
        if match is None:
            continue
        first, second = match.groups()
        if first in {"ontology", "concept", "bench"} and second is not None:
            commands.add(f"{first} {second}")
        else:
            commands.add(first)
    return commands


def test_readme_command_block_matches_build_parser() -> None:
    readme = README.read_text(encoding="utf-8")

    assert _leaf_commands(build_parser()) == EXPECTED_COMMANDS
    assert _documented_commands(readme) == EXPECTED_COMMANDS


def test_readme_and_skill_stamp_the_approved_investigate_measurement() -> None:
    for path in (README, SKILL):
        text = path.read_text(encoding="utf-8")
        for claim in MEASURED_CLAIMS:
            assert claim in text, f"{path.name} is missing {claim!r}"
        assert "post-fix/eligible" in text
        assert "Tier-1 ontology rebuild was parity preparation only" in text
        assert "target-band PASS" not in text


def test_public_docs_remove_superseded_behavior_claims() -> None:
    readme = README.read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")
    combined = readme + skill

    assert "Serve — fusion retrieval" not in combined
    assert "hooks + a 4-hour sweep" not in combined
    assert "Write each as an OKF file" not in combined
    assert "verified: machine-confirmed" not in skill
    assert "session-weaver recall" in skill
    assert "session-weaver winddown --session ID --from winddown.json" in skill
    assert "never hand-write OKF" in skill
    assert "Claude Code has no session-db MCP server registered" in skill


def test_readme_shows_the_ownership_marker_trailing_lf_exactly() -> None:
    readme = README.read_text(encoding="utf-8")
    assert 'b\'{"owner":"session-weaver","schema":1}\\n\'' in readme


def test_claims_audit_maps_public_claims_to_maintained_evidence() -> None:
    audit = CLAIMS_AUDIT.read_text(encoding="utf-8")

    for location in (
        "src/session_weaver/cli.py::build_parser",
        "src/session_weaver/cli.py::_doctor",
        "src/session_weaver/recall.py::recall",
        "src/session_weaver/concepts.py::ConceptService.winddown",
        "src/session_weaver/concept_schema.py::_fts_consistency",
        "src/session_weaver/ontology.py::ontology_status",
        "src/session_weaver/installer.py::install_skill",
        "docs/data/bench-baseline-phase-a.json",
        "tests/test_skill_sync.py",
    ):
        assert location in audit
    assert "INVESTIGATE" in audit
    assert "report-only" in audit
    assert "Phase B" in audit


def test_public_markdown_local_links_resolve() -> None:
    for document in (README, SKILL, CLAIMS_AUDIT):
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            assert (document.parent / relative).exists(), f"{document.name}: missing {target}"
