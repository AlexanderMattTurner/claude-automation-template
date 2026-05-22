"""Tests for .hooks/lint-skills.sh."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".hooks" / "lint-skills.sh"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    dest = tmp_path / "lint-skills.sh"
    shutil.copy2(SCRIPT, dest)
    dest.chmod(0o755)
    return tmp_path


def write_skill(sandbox: Path, name: str, body: str) -> Path:
    path = sandbox / ".claude" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def run_lint(sandbox: Path, *files: Path) -> subprocess.CompletedProcess:
    args = ["bash", str(sandbox / "lint-skills.sh"), *[str(f) for f in files]]
    return subprocess.run(args, cwd=sandbox, capture_output=True, text=True)


VALID_SKILL = """---
name: example
description: This skill does a thing. Activate when the user says foo.
---

# Example skill

## Examples

- foo -> bar
"""


def test_accepts_valid_skill(sandbox: Path) -> None:
    skill = write_skill(sandbox, "example", VALID_SKILL)
    result = run_lint(sandbox, skill)
    assert result.returncode == 0, result.stderr


def test_rejects_missing_frontmatter(sandbox: Path) -> None:
    skill = write_skill(sandbox, "broken", "# Just a heading\n")
    result = run_lint(sandbox, skill)
    assert result.returncode == 1
    assert "missing YAML frontmatter" in result.stderr


def test_rejects_missing_name(sandbox: Path) -> None:
    skill = write_skill(
        sandbox,
        "broken",
        "---\ndescription: A skill. With two sentences.\n---\n# body\n",
    )
    result = run_lint(sandbox, skill)
    assert result.returncode == 1
    assert "missing 'name:'" in result.stderr


def test_rejects_short_description(sandbox: Path) -> None:
    skill = write_skill(
        sandbox,
        "broken",
        "---\nname: x\ndescription: Tiny\n---\n# body\n",
    )
    result = run_lint(sandbox, skill)
    assert result.returncode == 1
    assert "description too short" in result.stderr


def test_rejects_flat_skill_file(sandbox: Path) -> None:
    flat = sandbox / ".claude" / "skills" / "flat.md"
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text(VALID_SKILL)
    result = run_lint(sandbox, flat)
    assert result.returncode == 1
    assert "flat file format" in result.stderr


def test_ignores_files_outside_skills(sandbox: Path) -> None:
    other = sandbox / "README.md"
    other.write_text("hi\n")
    result = run_lint(sandbox, other)
    assert result.returncode == 0, result.stderr


def test_warns_when_examples_missing(sandbox: Path) -> None:
    body = (
        "---\n"
        "name: example\n"
        "description: Does a thing. Activate when needed.\n"
        "---\n"
        "# Example\n"
    )
    skill = write_skill(sandbox, "example", body)
    result = run_lint(sandbox, skill)
    assert result.returncode == 0
    assert "Examples" in result.stderr
