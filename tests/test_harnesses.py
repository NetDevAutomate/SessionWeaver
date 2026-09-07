"""Harness registry: selection parsing and hub semantics."""

import pytest

from session_weaver.harnesses import HARNESSES, hub_dir, parse_harness_selection


def test_known_harnesses_cover_all_six():
    assert set(HARNESSES) == {"claude", "kiro", "grok", "codex", "opencode", "pi"}


def test_hub_readers_are_the_documented_three():
    readers = {name for name, h in HARNESSES.items() if h.reads_hub}
    assert readers == {"codex", "opencode", "pi"}


def test_parse_all_returns_every_harness():
    assert {h.name for h in parse_harness_selection("all")} == set(HARNESSES)


def test_parse_list_is_order_preserving_and_case_insensitive():
    chosen = parse_harness_selection("Kiro, claude")
    assert [h.name for h in chosen] == ["kiro", "claude"]


def test_parse_unknown_harness_is_an_error():
    with pytest.raises(ValueError, match="unknown harness 'zed'"):
        parse_harness_selection("zed")


def test_parse_empty_selection_is_an_error():
    with pytest.raises(ValueError, match="no harness selected"):
        parse_harness_selection(" , ")


def test_hub_dir_is_under_home(tmp_path):
    assert hub_dir(tmp_path) == tmp_path / ".agents" / "skills"
