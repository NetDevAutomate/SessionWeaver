"""CLI integration: the real entry point end-to-end against a fake home."""

import subprocess
import sys
from pathlib import Path

import pytest

from session_weaver.cli import main
from session_weaver.harnesses import hub_dir
from session_weaver.installer import SKILL_NAME


@pytest.fixture
def fake_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def test_install_all_then_status_and_uninstall(fake_home, capsys):
    assert main(["install", "--harness", "all"]) == 0
    assert (hub_dir(fake_home) / SKILL_NAME / "SKILL.md").is_file()
    out = capsys.readouterr().out
    assert "hub-install" in out and "link" in out and "skip" in out

    assert main(["status"]) == 0

    assert main(["uninstall", "--harness", "all", "--remove-hub"]) == 0
    assert not (hub_dir(fake_home) / SKILL_NAME).exists()


def test_install_dry_run_touches_nothing(fake_home):
    assert main(["install", "--harness", "kiro", "--dry-run"]) == 0
    assert not (fake_home / ".agents").exists()
    assert not (fake_home / ".kiro").exists()


def test_unknown_harness_is_a_usage_error(fake_home, capsys):
    assert main(["install", "--harness", "nonesuch"]) == 2
    assert "unknown harness" in capsys.readouterr().err


def test_conflict_exits_nonzero(fake_home, capsys):
    occupied = fake_home / ".kiro/skills" / SKILL_NAME
    occupied.mkdir(parents=True)
    (occupied / "keep.txt").write_text("x")

    assert main(["install", "--harness", "kiro"]) == 1
    assert "conflict" in capsys.readouterr().out


def test_copy_conflict_exits_nonzero_without_changing_existing_file(fake_home, capsys):
    occupied = fake_home / ".kiro/skills" / SKILL_NAME
    occupied.mkdir(parents=True)
    sentinel = occupied / "SKILL.md"
    sentinel.write_bytes(b"user-owned skill\x00\xff")

    assert main(["install", "--harness", "kiro", "--copy"]) == 1
    assert "conflict" in capsys.readouterr().out
    assert sentinel.read_bytes() == b"user-owned skill\x00\xff"


def test_copy_force_replaces_named_target_and_reports_outcome(fake_home, capsys):
    occupied = fake_home / ".kiro/skills" / SKILL_NAME
    occupied.mkdir(parents=True)
    sentinel = occupied / "SKILL.md"
    sentinel.write_bytes(b"replace me")
    extra = occupied / "user-only.txt"
    extra.write_bytes(b"remove with forced target")
    sibling = occupied.parent / "keep-me.txt"
    sibling.write_bytes(b"outside named target")

    assert main(["install", "--harness", "kiro", "--copy", "--force"]) == 0
    assert "replaced existing target for kiro" in capsys.readouterr().out
    assert sentinel.read_bytes() != b"replace me"
    assert not extra.exists()
    assert sibling.read_bytes() == b"outside named target"


def test_force_without_copy_is_a_usage_error(fake_home, capsys):
    assert main(["install", "--harness", "kiro", "--force"]) == 2
    assert "--force requires --copy" in capsys.readouterr().err


def test_uninstall_plain_file_is_a_visible_conflict(fake_home, capsys):
    target = fake_home / ".kiro/skills" / SKILL_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user-owned plain file\x00\xff")

    assert main(["uninstall", "--harness", "kiro"]) == 1
    assert "conflict" in capsys.readouterr().out
    assert target.read_bytes() == b"user-owned plain file\x00\xff"


def test_console_script_shape_is_importable_and_runs():
    """The installed-entry-point path: python -m equivalent smoke."""
    code = "from session_weaver.cli import main; raise SystemExit(main(['--version']))"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "session-weaver" in proc.stdout


def test_doctor_reports_missing_store(fake_home, capsys):
    rc = main(["doctor", "--db", str(fake_home / "nope.db")])
    out = capsys.readouterr().out
    assert "session store missing" in out
    assert rc == 1
