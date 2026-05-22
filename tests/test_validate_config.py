"""Tests for .github/scripts/validate-config.sh."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "validate-config.sh"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Sandbox with the validate-config script available at the expected path."""
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    dest = tmp_path / ".github" / "scripts" / "validate-config.sh"
    shutil.copy2(SCRIPT, dest)
    dest.chmod(0o755)
    return tmp_path


def write_settings(sandbox: Path, settings: dict) -> None:
    (sandbox / ".claude").mkdir(exist_ok=True)
    (sandbox / ".claude" / "settings.json").write_text(json.dumps(settings))


def make_hook(sandbox: Path, rel_path: str, executable: bool = True) -> Path:
    path = sandbox / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n")
    if executable:
        path.chmod(0o755)
    else:
        path.chmod(0o644)
    return path


def run_validator(sandbox: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", ".github/scripts/validate-config.sh"],
        cwd=sandbox,
        capture_output=True,
        text=True,
    )


def test_passes_with_valid_config(sandbox: Path) -> None:
    write_settings(
        sandbox,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR"/.claude/hooks/session-setup.sh',
                            }
                        ]
                    }
                ]
            }
        },
    )
    make_hook(sandbox, ".claude/hooks/session-setup.sh")
    make_hook(sandbox, ".hooks/pre-commit")

    result = run_validator(sandbox)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout


def test_fails_when_settings_missing(sandbox: Path) -> None:
    make_hook(sandbox, ".hooks/pre-commit")
    result = run_validator(sandbox)
    assert result.returncode == 1
    assert ".claude/settings.json not found" in result.stdout


def test_fails_when_referenced_hook_missing(sandbox: Path) -> None:
    write_settings(
        sandbox,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": '"$CLAUDE_PROJECT_DIR"/.claude/hooks/missing.sh',
                            }
                        ]
                    }
                ]
            }
        },
    )
    make_hook(sandbox, ".hooks/pre-commit")
    result = run_validator(sandbox)
    assert result.returncode == 1
    assert "missing.sh" in result.stdout


def test_fails_when_hook_not_executable(sandbox: Path) -> None:
    write_settings(sandbox, {"hooks": {}})
    make_hook(sandbox, ".hooks/pre-commit", executable=False)
    result = run_validator(sandbox)
    assert result.returncode == 1
    assert "not executable" in result.stdout
