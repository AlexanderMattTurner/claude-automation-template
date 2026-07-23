"""Tests for .claude/hooks/safe-launch.sh — the resilient PreToolUse launcher.

Focus: the fast path runs the target THROUGH its interpreter, so a target that
lost its +x bit still runs the guard (a bare `exec "$target"` would exit 126,
which Claude Code treats as non-blocking → the guarded tool runs unchecked).
"""

import json
import os
import stat
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
LAUNCH = HOOKS_DIR / "safe-launch.sh"


def _run(target: Path, project: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(LAUNCH), str(target)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
    )


def test_valid_target_without_exec_bit_still_runs(tmp_path: Path) -> None:
    """A syntactically-valid target with its +x bit stripped must still run — via
    `exec bash "$target"`. RED without the interpreter-explicit exec: the bare
    `exec "$target"` fails 126 and the target never runs."""
    sentinel = tmp_path / "ran"
    target = tmp_path / "hook.sh"
    target.write_text(f'#!/bin/bash\necho ran > "{sentinel}"\nexit 0\n')
    # Explicitly clear every execute bit.
    target.chmod(target.stat().st_mode & ~0o111)
    result = _run(
        target, tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    )
    assert result.returncode == 0, result.stderr
    assert sentinel.exists(), "target did not run — interpreter-explicit exec missing"
    # The fast path ran the target, so no degraded 'ask' verdict was emitted.
    assert "permissionDecision" not in result.stdout


def test_broken_syntax_target_degrades_to_ask(tmp_path: Path) -> None:
    """A target that fails its syntax check degrades to an 'ask' verdict rather
    than blocking the session."""
    target = tmp_path / "broken.sh"
    target.write_text("#!/bin/bash\nif [ ; then\n")  # invalid bash
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    result = _run(
        target, tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}
    )
    assert result.returncode == 0, result.stderr
    verdict = json.loads(result.stdout)
    assert verdict["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_broken_target_allows_self_repair_edit(tmp_path: Path) -> None:
    """When the target is broken, an edit to a hook under .claude/hooks is allowed
    (exit 0, no verdict) so the broken hook can be repaired in-session."""
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    target = tmp_path / ".claude" / "hooks" / "broken.sh"
    target.write_text("#!/bin/bash\nif [ ; then\n")
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_path / ".claude" / "hooks" / "broken.sh")},
    }
    result = _run(target, tmp_path, payload)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
