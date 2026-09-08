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


def _file_bytes(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_install_writes_stable_hub_ownership_marker(skill_source, home):
    report = install_skill([], skill_source=skill_source, home=home)

    marker = hub_dir(home) / SKILL_NAME / ".session-weaver-owned.json"
    assert marker.read_bytes() == b'{"owner":"session-weaver","schema":1}\n'
    assert not report.conflicts


def test_install_preserves_unowned_hub_and_stops_before_wiring(skill_source, home):
    hub_skill = hub_dir(home) / SKILL_NAME
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_bytes(b"user-owned hub\x00\xff")
    nested = hub_skill / "nested"
    nested.mkdir()
    (nested / "settings.bin").write_bytes(b"\x00user settings\xff")
    before = _file_bytes(hub_skill)

    report = install_skill(linkers(), skill_source=skill_source, home=home)

    assert len(report.conflicts) == 1
    assert report.conflicts[0].target == hub_skill
    assert "unowned" in report.conflicts[0].detail
    assert _file_bytes(hub_skill) == before
    assert not (hub_skill / ".session-weaver-owned.json").exists()
    for harness in linkers():
        target = harness.resolved_skills_dir(home) / SKILL_NAME
        assert not target.is_symlink()
        assert not target.exists()


@pytest.mark.parametrize(
    "marker_bytes",
    [None, b'{"owner":"someone-else","schema":1}\n'],
    ids=["missing-marker", "invalid-marker"],
)
def test_remove_hub_preserves_unowned_bytes_and_reports_conflict(home, marker_bytes):
    hub_skill = hub_dir(home) / SKILL_NAME
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_bytes(b"user-owned hub\x00\xff")
    nested = hub_skill / "nested"
    nested.mkdir()
    (nested / "settings.bin").write_bytes(b"\x00user settings\xff")
    if marker_bytes is not None:
        (hub_skill / ".session-weaver-owned.json").write_bytes(marker_bytes)
    before = _file_bytes(hub_skill)

    report = uninstall_skill([], home=home, remove_hub=True)

    assert len(report.conflicts) == 1
    assert report.conflicts[0].target == hub_skill
    assert "unowned" in report.conflicts[0].detail
    assert _file_bytes(hub_skill) == before


def test_remove_hub_removes_directory_with_valid_ownership_marker(home):
    hub_skill = hub_dir(home) / SKILL_NAME
    hub_skill.mkdir(parents=True)
    (hub_skill / "SKILL.md").write_bytes(b"session-weaver skill")
    (hub_skill / ".session-weaver-owned.json").write_bytes(
        b'{"owner":"session-weaver","schema":1}\n'
    )

    report = uninstall_skill([], home=home, remove_hub=True)

    assert not hub_skill.exists()
    assert any(
        action.kind == "hub-install" and action.detail == "hub copy removed"
        for action in report.actions
    )


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


def test_hub_readers_are_also_skipped_in_copy_mode(skill_source, home):
    readers = [h for h in HARNESSES.values() if h.reads_hub]

    report = install_skill(readers, skill_source=skill_source, home=home, mode="copy")

    assert [action.kind for action in report.actions].count("skip") == len(readers)
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


def test_copy_mode_writes_stable_ownership_marker(skill_source, home):
    harness = linkers()[0]

    install_skill([harness], skill_source=skill_source, home=home, mode="copy")

    marker = harness.resolved_skills_dir(home) / SKILL_NAME / ".session-weaver-owned.json"
    assert marker.read_bytes() == b'{"owner":"session-weaver","schema":1}\n'


def test_copy_mode_conflict_preserves_every_pre_existing_byte(skill_source, home):
    harness = linkers()[0]
    target = harness.resolved_skills_dir(home) / SKILL_NAME
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"user-owned skill\x00\xff")
    nested = target / "nested"
    nested.mkdir()
    (nested / "settings.json").write_bytes(b'{"owner":"user"}\n')
    before = {
        path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()
    }

    report = install_skill([harness], skill_source=skill_source, home=home, mode="copy")

    after = {
        path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()
    }
    assert len(report.conflicts) == 1
    assert after == before


def test_copy_mode_force_replaces_only_the_named_target(skill_source, home):
    harness = linkers()[0]
    target = harness.resolved_skills_dir(home) / SKILL_NAME
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"replace me")
    (target / "user-only.txt").write_bytes(b"remove with forced target")
    sibling = target.parent / "keep-me.txt"
    sibling.write_bytes(b"outside named target")

    report = install_skill(
        [harness],
        skill_source=skill_source,
        home=home,
        mode="copy",
        force=True,
    )

    assert (target / "SKILL.md").read_text().endswith("test skill\n")
    assert not (target / "user-only.txt").exists()
    assert sibling.read_bytes() == b"outside named target"
    assert any(
        action.kind == "copy" and action.detail == f"replaced existing target for {harness.name}"
        for action in report.actions
    )


def test_force_requires_copy_mode(skill_source, home):
    with pytest.raises(ValueError, match="force requires copy mode"):
        install_skill(linkers(), skill_source=skill_source, home=home, force=True)


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


def test_uninstall_preserves_symlink_not_pointing_to_sessionweaver_hub(home):
    harness = linkers()[0]
    elsewhere = home / "user-managed-skill"
    elsewhere.mkdir(parents=True)
    target = harness.resolved_skills_dir(home) / SKILL_NAME
    target.parent.mkdir(parents=True)
    target.symlink_to(elsewhere)
    original_link = target.readlink()

    report = uninstall_skill([harness], home=home)

    assert len(report.conflicts) == 1
    assert target.is_symlink()
    assert target.readlink() == original_link


def test_uninstall_reports_plain_file_as_unowned_and_preserves_bytes(home):
    harness = linkers()[0]
    target = harness.resolved_skills_dir(home) / SKILL_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user-owned plain file\x00\xff")

    report = uninstall_skill([harness], home=home)

    assert len(report.conflicts) == 1
    assert "plain file is unowned" in report.conflicts[0].detail
    assert target.read_bytes() == b"user-owned plain file\x00\xff"


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


@pytest.mark.parametrize(
    "marker_bytes",
    [None, b'{"owner":"someone-else","schema":1}\n'],
    ids=["missing-marker", "invalid-marker"],
)
def test_uninstall_preserves_directory_without_valid_ownership_marker(
    home,
    marker_bytes,
):
    harness = linkers()[0]
    target = harness.resolved_skills_dir(home) / SKILL_NAME
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"user-owned skill")
    if marker_bytes is not None:
        (target / ".session-weaver-owned.json").write_bytes(marker_bytes)
    before = {
        path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()
    }

    report = uninstall_skill([harness], home=home)

    after = {
        path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()
    }
    assert len(report.conflicts) == 1
    assert after == before


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
