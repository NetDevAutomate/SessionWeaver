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
