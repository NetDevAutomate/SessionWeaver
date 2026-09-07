"""The repo-root SKILL.md (what GitHub shows) and the packaged skill must match.

Two copies exist on purpose — the root file for repository visibility, the
package-data file for what `session-weaver install` actually ships — and this
guard is what keeps "on purpose" from becoming "drifted".
"""

from pathlib import Path

from session_weaver.installer import packaged_skill_dir

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_root_skill_md_matches_packaged_copy():
    root = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    packaged = (packaged_skill_dir() / "SKILL.md").read_text(encoding="utf-8")
    assert root == packaged, (
        "SKILL.md and src/session_weaver/data/skills/session-weaver/SKILL.md "
        "have drifted — edit one, copy to the other (or fix the installer to "
        "ship the intended version)"
    )
