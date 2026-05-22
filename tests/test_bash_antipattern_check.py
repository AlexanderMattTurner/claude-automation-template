"""Tests for .claude/hooks/bash-antipattern-check.sh."""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "bash-antipattern-check.sh"


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


BLOCKED = [
    ("plain", "cmd || true"),
    ("no-space", "cmd||true"),
    ("colon noop", "cmd || :"),
    ("no-space colon", "cmd||:"),
    ("extra whitespace", "cmd   ||   true"),
    ("after redirect", "cmd 2>/dev/null || true"),
    ("trailing semicolon", "cmd || true; echo done"),
    ("trailing ampersand", "cmd || true & next"),
    ("trailing pipe", "cmd || true | tee log"),
    ("inside pipeline", "foo | bar || true"),
    ("double-quoted arg before", 'echo "hello" || true'),
]

ALLOWED = [
    ("literal in single quotes", "grep '|| true' file"),
    ("literal in double quotes", 'grep "|| true" file'),
    ("different fallback", "cmd || handle_error"),
    ("true as sole command", "true"),
    ("truefoo not a word boundary", "cmd || truefoo"),
    ("falsey not matched", "cmd || false"),
    ("colon followed by word", "cmd || :wq"),
    ("no shell operator at all", "echo hello world"),
]


@pytest.mark.parametrize("label, command", BLOCKED, ids=[c[0] for c in BLOCKED])
def test_blocks_antipatterns(label: str, command: str) -> None:
    result = run_hook({"tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 2, (
        f"[{label}] expected block (exit 2), got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert "|| true" in result.stderr, (
        f"[{label}] block message should mention '|| true'; got: {result.stderr!r}"
    )


@pytest.mark.parametrize("label, command", ALLOWED, ids=[c[0] for c in ALLOWED])
def test_allows_benign_commands(label: str, command: str) -> None:
    result = run_hook({"tool_name": "Bash", "tool_input": {"command": command}})
    assert result.returncode == 0, (
        f"[{label}] expected allow (exit 0), got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )


def test_skips_non_bash_tools() -> None:
    """The hook should be a no-op for any tool other than Bash, even if the
    payload happens to contain '|| true' somewhere."""
    result = run_hook({"tool_name": "Read", "tool_input": {"file_path": "cmd || true"}})
    assert result.returncode == 0, result.stderr


def test_empty_command_is_allowed() -> None:
    result = run_hook({"tool_name": "Bash", "tool_input": {"command": ""}})
    assert result.returncode == 0, result.stderr


def test_missing_tool_input_is_allowed() -> None:
    """Defensive: malformed payloads shouldn't crash the hook."""
    result = run_hook({"tool_name": "Bash"})
    assert result.returncode == 0, result.stderr
