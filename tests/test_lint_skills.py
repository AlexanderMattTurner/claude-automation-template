"""Tests for .hooks/lint-skills.sh."""

import subprocess
from pathlib import Path

import pytest


def write_skill(sandbox: Path, name: str, body: str) -> Path:
    path = sandbox / ".claude" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def run_lint(sandbox: Path, copy_script, *files: Path) -> subprocess.CompletedProcess:
    script = copy_script("lint-skills.sh", sandbox)
    args = ["bash", str(script), *[str(f) for f in files]]
    return subprocess.run(args, cwd=sandbox, capture_output=True, text=True)


VALID_SKILL = """---
name: example
description: This skill does a thing. Activate when the user says foo.
---

# Example skill

## Examples

- foo -> bar
"""


def test_accepts_valid_skill(tmp_path: Path, copy_script) -> None:
    skill = write_skill(tmp_path, "example", VALID_SKILL)
    result = run_lint(tmp_path, copy_script, skill)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "body, expected_stderr_snippet",
    [
        ("# Just a heading\n", "missing YAML frontmatter"),
        (
            "---\ndescription: A skill. With two sentences.\n---\n# body\n",
            "missing 'name:'",
        ),
        ("---\nname: x\ndescription: Tiny\n---\n# body\n", "description too short"),
    ],
    ids=["no-frontmatter", "no-name", "short-description"],
)
def test_rejects_invalid_skill(
    tmp_path: Path, copy_script, body: str, expected_stderr_snippet: str
) -> None:
    skill = write_skill(tmp_path, "broken", body)
    result = run_lint(tmp_path, copy_script, skill)
    assert result.returncode == 1
    assert expected_stderr_snippet in result.stderr


def test_rejects_flat_skill_file(tmp_path: Path, copy_script) -> None:
    flat = tmp_path / ".claude" / "skills" / "flat.md"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text(VALID_SKILL, encoding="utf-8")
    result = run_lint(tmp_path, copy_script, flat)
    assert result.returncode == 1
    assert "flat file format" in result.stderr


def test_ignores_files_outside_skills(tmp_path: Path, copy_script) -> None:
    other = tmp_path / "README.md"
    other.write_text("hi\n", encoding="utf-8")
    result = run_lint(tmp_path, copy_script, other)
    assert result.returncode == 0, result.stderr


def test_warns_when_examples_missing(tmp_path: Path, copy_script) -> None:
    body = (
        "---\n"
        "name: example\n"
        "description: Does a thing. Activate when needed.\n"
        "---\n"
        "# Example\n"
    )
    skill = write_skill(tmp_path, "example", body)
    result = run_lint(tmp_path, copy_script, skill)
    assert result.returncode == 0
    assert "Examples" in result.stderr


def test_rejects_unclosed_frontmatter(tmp_path: Path, copy_script) -> None:
    """A skill missing the closing '---' delimiter should be rejected."""
    body = "---\nname: x\ndescription: A skill. With two sentences.\n# body without closing ---\n"
    skill = write_skill(tmp_path, "broken", body)
    result = run_lint(tmp_path, copy_script, skill)
    assert result.returncode == 1
    assert "closing" in result.stderr


def test_an_all_comment_frontmatter_reports_instead_of_aborting(
    tmp_path: Path, copy_script
) -> None:
    """A frontmatter holding only comments must reach the field checks, which
    name what is missing, rather than abort the linter with no message."""
    skill = write_skill(tmp_path, "comments", "---\n# a comment\n---\n# body\n")
    result = run_lint(tmp_path, copy_script, skill)
    assert result.returncode == 1
    assert "missing 'name:'" in result.stderr
    assert "missing 'description:'" in result.stderr


def test_the_next_key_does_not_count_toward_the_description(
    tmp_path: Path, copy_script
) -> None:
    """The periods in the key after `description:` are not part of it."""
    body = "---\nname: x\ndescription: Tiny\nversion: 1.2.3\n---\n# body\n"
    skill = write_skill(tmp_path, "short", body)
    result = run_lint(tmp_path, copy_script, skill)
    assert result.returncode == 1
    assert "description too short" in result.stderr


def test_a_continued_description_counts_every_line(tmp_path: Path, copy_script) -> None:
    body = "---\nname: x\ndescription: >\n  One sentence.\n  Two sentences.\n---\n# b\n"
    skill = write_skill(tmp_path, "folded", body)
    result = run_lint(tmp_path, copy_script, skill)
    assert result.returncode == 0, result.stderr
