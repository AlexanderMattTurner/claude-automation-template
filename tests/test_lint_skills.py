"""Tests for .github/scripts/lint-skills.py.

Drives the shipped script itself (not a copy) over sandbox skill trees and
asserts the observable verdict: exit code plus the specific stderr line.

Two cases pin misfires of the retired shell implementation and fail against it:
``test_rejects_description_whose_periods_come_from_the_next_key`` (its
``sed -n '/^description:/,/^[a-z]/p'`` range swallowed ``version: 1.2.3``, whose
two dots satisfied the sentence check) and ``test_rejects_invalid_yaml_frontmatter``
(``name: "unterminated`` parses as nothing at all, yet every text-scan check
matched and the file was accepted).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "lint-skills.py"


def write_skill(sandbox: Path, name: str, body: str) -> Path:
    path = sandbox / ".claude" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def run_lint(sandbox: Path, *files: Path) -> subprocess.CompletedProcess:
    args = [sys.executable, str(SCRIPT), *[str(f) for f in files]]
    return subprocess.run(args, cwd=sandbox, capture_output=True, text=True)


VALID_SKILL = """---
name: example
description: This skill does a thing. Activate when the user says foo.
---

# Example skill

## Examples

- foo -> bar
"""


def test_accepts_valid_skill(tmp_path: Path) -> None:
    skill = write_skill(tmp_path, "example", VALID_SKILL)
    result = run_lint(tmp_path, skill)
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
        ("---\nname: x\n---\n# body\n", "missing 'description:'"),
    ],
    ids=["no-frontmatter", "no-name", "short-description", "no-description"],
)
def test_rejects_invalid_skill(
    tmp_path: Path, body: str, expected_stderr_snippet: str
) -> None:
    skill = write_skill(tmp_path, "broken", body)
    result = run_lint(tmp_path, skill)
    assert result.returncode == 1
    assert expected_stderr_snippet in result.stderr


def test_rejects_description_whose_periods_come_from_the_next_key(
    tmp_path: Path,
) -> None:
    """`description: hi` has no sentence; `version: 1.2.3` must not lend it two."""
    body = "---\nname: demo\ndescription: hi\nversion: 1.2.3\n---\n# body\n"
    skill = write_skill(tmp_path, "demo", body)
    result = run_lint(tmp_path, skill)
    assert result.returncode == 1
    assert "description too short" in result.stderr


def test_accepts_folded_block_scalar_description(tmp_path: Path) -> None:
    """A `description: >` value is one scalar spanning several lines."""
    body = (
        "---\n"
        "# prettier-ignore\n"
        "name: demo\n"
        "description: >\n"
        "  Does a thing.\n"
        "  Activate when the user says foo.\n"
        "---\n"
        "# body\n"
    )
    skill = write_skill(tmp_path, "demo", body)
    result = run_lint(tmp_path, skill)
    assert result.returncode == 0, result.stderr


def test_rejects_frontmatter_that_is_not_a_mapping(tmp_path: Path) -> None:
    body = "---\n- just\n- a list\n---\n# body\n"
    skill = write_skill(tmp_path, "broken", body)
    result = run_lint(tmp_path, skill)
    assert result.returncode == 1
    assert "not a YAML mapping" in result.stderr


def test_rejects_invalid_yaml_frontmatter(tmp_path: Path) -> None:
    body = '---\nname: "unterminated\ndescription: A thing. Activate on foo.\n---\n'
    skill = write_skill(tmp_path, "broken", body)
    result = run_lint(tmp_path, skill)
    assert result.returncode == 1
    assert "not valid YAML" in result.stderr


def test_rejects_flat_skill_file(tmp_path: Path) -> None:
    flat = tmp_path / ".claude" / "skills" / "flat.md"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text(VALID_SKILL)
    result = run_lint(tmp_path, flat)
    assert result.returncode == 1
    assert "flat file format" in result.stderr


def test_ignores_files_outside_skills(tmp_path: Path) -> None:
    other = tmp_path / "README.md"
    other.write_text("hi\n")
    result = run_lint(tmp_path, other)
    assert result.returncode == 0, result.stderr


def test_ignores_supporting_files_beside_a_skill(tmp_path: Path) -> None:
    reference = tmp_path / ".claude" / "skills" / "example" / "reference.md"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_text("no frontmatter here\n")
    result = run_lint(tmp_path, reference)
    assert result.returncode == 0, result.stderr


def test_warns_when_examples_missing(tmp_path: Path) -> None:
    body = (
        "---\n"
        "name: example\n"
        "description: Does a thing. Activate when needed.\n"
        "---\n"
        "# Example\n"
    )
    skill = write_skill(tmp_path, "example", body)
    result = run_lint(tmp_path, skill)
    assert result.returncode == 0
    assert "Examples" in result.stderr


def test_rejects_unclosed_frontmatter(tmp_path: Path) -> None:
    """A skill missing the closing '---' delimiter should be rejected."""
    body = "---\nname: x\ndescription: A skill. With two sentences.\n# body without closing ---\n"
    skill = write_skill(tmp_path, "broken", body)
    result = run_lint(tmp_path, skill)
    assert result.returncode == 1
    assert "closing" in result.stderr


def test_real_skills_pass() -> None:
    """The shipped skills are the clean-tree dogfood; a vacuous glob is caught."""
    skills = sorted((REPO_ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    assert skills, "no shipped SKILL.md files were found"
    result = run_lint(REPO_ROOT, *skills)
    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr
