"""Installer semantics against a fake home: hub, links, conflicts, idempotence."""

from pathlib import Path

import pytest

from session_weaver.harnesses import HARNESSES, hub_dir
from session_weaver.installer import (
    SKILL_NAME,
    install_skill,
    packaged_skill_dir,
    status,
    uninstall_skill,
)


@pytest.fixture
def skill_source(tmp_path) -> Path:
    src = tmp_path / "skill-src"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: session-weaver\n---\ntest skill\n")
    return src


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "home"


def linkers():
    return [h for h in HARNESSES.values() if not h.reads_hub]


def test_packaged_skill_ships_a_skill_md():
    assert (packaged_skill_dir() / "SKILL.md").is_file()


def test_install_creates_hub_copy_and_harness_links(skill_source, home):
    report = install_skill(linkers(), skill_source=skill_source, home=home)

    hub_skill = hub_dir(home) / SKILL_NAME
    assert (hub_skill / "SKILL.md").read_text().endswith("test skill\n")
    for harness in linkers():
        link = harness.resolved_skills_dir(home) / SKILL_NAME
        assert link.is_symlink() and link.resolve() == hub_skill.resolve()
    assert not report.conflicts


def test_hub_readers_are_skipped_not_linked(skill_source, home):
    readers = [h for h in HARNESSES.values() if h.reads_hub]
    report = install_skill(readers, skill_source=skill_source, home=home)

    assert all(a.kind in ("hub-install", "skip") for a in report.actions)
    for harness in readers:
        assert not (harness.resolved_skills_dir(home) / SKILL_NAME).exists()


def test_install_is_idempotent(skill_source, home):
    install_skill(linkers(), skill_source=skill_source, home=home)
    second = install_skill(linkers(), skill_source=skill_source, home=home)

    kinds = {a.kind for a in second.actions}
    assert "conflict" not in kinds
    assert all(a.kind == "skip" for a in second.actions if a.kind not in ("hub-install",))


def test_wrong_symlink_is_repointed(skill_source, home):
    harness = linkers()[0]
    elsewhere = home / "elsewhere"
    elsewhere.mkdir(parents=True)
    link = harness.resolved_skills_dir(home) / SKILL_NAME
    link.parent.mkdir(parents=True)
    link.symlink_to(elsewhere)

    report = install_skill([harness], skill_source=skill_source, home=home)

    assert link.resolve() == (hub_dir(home) / SKILL_NAME).resolve()
    assert any(a.detail == "repointed to the hub" for a in report.actions)


def test_real_directory_in_the_way_is_a_conflict_and_untouched(skill_source, home):
    harness = linkers()[0]
    occupied = harness.resolved_skills_dir(home) / SKILL_NAME
    occupied.mkdir(parents=True)
    sentinel = occupied / "precious.txt"
    sentinel.write_text("do not delete")

    report = install_skill([harness], skill_source=skill_source, home=home)

    assert len(report.conflicts) == 1
    assert sentinel.read_text() == "do not delete"


def test_copy_mode_duplicates_instead_of_linking(skill_source, home):
    harness = linkers()[0]
    report = install_skill([harness], skill_source=skill_source, home=home, mode="copy")

    target = harness.resolved_skills_dir(home) / SKILL_NAME
    assert target.is_dir() and not target.is_symlink()
    assert (target / "SKILL.md").is_file()
    assert not report.conflicts


def test_dry_run_changes_nothing(skill_source, home):
    report = install_skill(linkers(), skill_source=skill_source, home=home, dry_run=True)

    assert not (hub_dir(home) / SKILL_NAME).exists()
    assert all(not h.resolved_skills_dir(home).exists() for h in linkers())
    assert report.actions  # still reports what it WOULD do


def test_uninstall_removes_links_and_optionally_hub(skill_source, home):
    install_skill(linkers(), skill_source=skill_source, home=home)
    uninstall_skill(linkers(), home=home, remove_hub=True)

    assert not (hub_dir(home) / SKILL_NAME).exists()
    for harness in linkers():
        assert not (harness.resolved_skills_dir(home) / SKILL_NAME).exists()


def test_status_reports_hub_and_links(skill_source, home):
    install_skill(linkers(), skill_source=skill_source, home=home)
    report = status(list(HARNESSES.values()), home=home)

    by_kind = {a.kind for a in report.actions}
    assert "hub-install" in by_kind  # hub present
    assert "link" in by_kind  # linked harnesses
    assert "skip" in by_kind  # hub readers


def test_copy_mode_over_existing_symlink_is_a_conflict(skill_source, home):
    harness = linkers()[0]
    install_skill([harness], skill_source=skill_source, home=home)  # symlink first
    report = install_skill([harness], skill_source=skill_source, home=home, mode="copy")

    assert len(report.conflicts) == 1
    assert "remove it before copy mode" in report.conflicts[0].detail


def test_uninstall_removes_copied_directory(skill_source, home):
    harness = linkers()[0]
    install_skill([harness], skill_source=skill_source, home=home, mode="copy")
    report = uninstall_skill([harness], home=home)

    assert not (harness.resolved_skills_dir(home) / SKILL_NAME).exists()
    assert any(a.kind == "copy" and "removed" in a.detail for a in report.actions)


def test_uninstall_dry_run_reports_but_keeps_links(skill_source, home):
    install_skill(linkers(), skill_source=skill_source, home=home)
    uninstall_skill(linkers(), home=home, remove_hub=True, dry_run=True)

    assert (hub_dir(home) / SKILL_NAME).exists()
    for harness in linkers():
        assert (harness.resolved_skills_dir(home) / SKILL_NAME).is_symlink()


def test_status_flags_a_link_pointing_away_from_the_hub(skill_source, home):
    harness = linkers()[0]
    elsewhere = home / "elsewhere-skill"
    elsewhere.mkdir(parents=True)
    link = harness.resolved_skills_dir(home) / SKILL_NAME
    link.parent.mkdir(parents=True)
    link.symlink_to(elsewhere)

    report = status([harness], home=home)

    assert any("NOT hub" in a.detail for a in report.actions)


def test_install_rejects_source_without_skill_md(home, tmp_path):
    empty = tmp_path / "empty-src"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        install_skill(linkers(), skill_source=empty, home=home)


def test_install_rejects_unknown_mode(skill_source, home):
    with pytest.raises(ValueError, match="mode must be"):
        install_skill(linkers(), skill_source=skill_source, home=home, mode="hardlink")
