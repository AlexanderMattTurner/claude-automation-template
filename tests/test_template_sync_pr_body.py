"""Tests for .github/scripts/template-sync-pr-body.sh.

The body used to be a nested `format()` chain inside template-sync.yaml, which
no test could reach. These drive the script and assert on the rendered
markdown.
"""

import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "template-sync-pr-body.sh"


def render(tmp_path: Path, **env: str) -> str:
    out = tmp_path / "body.md"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "TEMPLATE_REPO": "Owner/tmpl",
            "TEMPLATE_SHA_SHORT": "abc1234",
            "PR_BODY_PATH": str(out),
            **env,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return out.read_text(encoding="utf-8")


def test_a_multi_entry_list_is_counted_and_listed_whole(tmp_path: Path) -> None:
    """A splitter that reads one entry reports `1 file(s)` for a twelve-file
    sync and silently drops eleven bullets."""
    body = render(tmp_path, CHANGED_FILES="a.md b.md c.md")
    assert "Syncs 3 file(s)" in body
    for name in ("a.md", "b.md", "c.md"):
        assert f"- `{name}`" in body


def test_the_explanation_leads_and_the_noise_is_folded(tmp_path: Path) -> None:
    """Inverted pyramid: why the tree changed, then what needs a human, and the
    conflict-free merges behind a fold at the end."""
    body = render(
        tmp_path,
        CHANGED_FILES="a.md",
        CHANGELOG="- `a1b2c3d` feat: a thing\n  - `a.md`",
        DOWNGRADE_REPORT="- `a.md` lost 3 lines",
        AUTO_MERGED_FILES="a.md",
    )
    assert body.index("What changed, and why") < body.index("Adopter-ahead")
    assert body.index("Adopter-ahead") < body.index("<details>")
    assert "merged with no conflict" in body


def test_each_section_renders_when_its_variable_is_set(tmp_path: Path) -> None:
    """The absence test below passes just as well if a section can NEVER render,
    so a typo'd env name would go unnoticed. Pin each one positively."""
    for var, heading in (
        ("DOWNGRADE_REPORT", "Adopter-ahead"),
        ("CONFLICT_REPORT", "Conflicts needing a merge decision"),
        ("DELETED_FILES", "Deleted in the template"),
        ("DECLINED_FILES", "Declined"),
        ("INERT_ENTRIES", "Inert EXCLUDE_PATHS"),
        ("AUTO_MERGED_FILES", "merged with no conflict"),
    ):
        body = render(tmp_path, CHANGED_FILES="a.md", **{var: "z.md"})
        assert heading in body, var
        assert "z.md" in body, var


def test_a_section_with_nothing_to_say_is_absent(tmp_path: Path) -> None:
    """An empty section costs the reader a scan and says nothing; the old body
    rendered several of them unconditionally."""
    body = render(tmp_path, CHANGED_FILES="a.md")
    for heading in (
        "Adopter-ahead",
        "Declined",
        "Inert",
        "Deleted in the template",
        "Conflicts",
    ):
        assert heading not in body
    assert "None" not in body
