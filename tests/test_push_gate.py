"""Behavioral tests for .claude/hooks/push-gate.mjs.

The gate runs pre-push-check.sh before a push or a new pull request and blocks
the call when a check fails. Blocking means the deny body on stdout: Claude Code
runs a tool call whose hook merely exited 1. Each case drives the real hook over
its stdin protocol, in a sandbox project whose only check is a stub `ruff`.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

SESSION = "push-gate-sess"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with the real hooks, the predicate they call, and a pyproject so
    pre-push-check.sh runs `ruff` — a stub that records its call."""
    root = tmp_path / "proj"
    shutil.copytree(REPO_ROOT / ".claude" / "hooks", root / ".claude" / "hooks")
    # The bash parser the hooks load at fire time: web-tree-sitter and
    # tree-sitter-bash, from the repo's own install.
    (root / "node_modules").symlink_to(REPO_ROOT / "node_modules")
    scripts = root / ".github" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(REPO_ROOT / ".github" / "scripts" / "script-configured.sh", scripts)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    return root


def stub_ruff(tmp_path: Path, exit_code: int) -> str:
    """A PATH whose `ruff` (a stub: the real linter's verdict is not under test)
    logs its call and exits `exit_code`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    ruff = bin_dir / "ruff"
    ruff.write_text(
        f'#!/bin/bash\necho called >>"{tmp_path / "ruff.log"}"\n'
        f'echo "E501 line too long"\nexit {exit_code}\n',
        encoding="utf-8",
    )
    ruff.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def run_gate(
    project: Path, tmp_path: Path, call: dict, exit_code: int, command=None
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": stub_ruff(tmp_path, exit_code),
        "CLAUDE_PROJECT_DIR": str(project),
        "COMPLETION_CHECK_STATE_DIR": str(tmp_path / "state"),
    }
    argv = command or ["node", str(project / ".claude" / "hooks" / "push-gate.mjs")]
    result = subprocess.run(
        argv,
        input=json.dumps({"session_id": SESSION, **call}),
        capture_output=True,
        text=True,
        env=env,
        cwd=project,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


GATED = [
    "git push -u origin x",
    "git -C /tmp/claude-worktrees/a push origin x",
    'git -C "/a b" push',
    "git -c core.hooksPath=.hooks push",
    "git --no-pager push",
    "GIT_TRACE=1 git push",
    "cd repo && git push",
    "/usr/bin/git push",
    "gh pr create --fill",
    "gh -R o/r pr create",
    "GH_TOKEN=x gh pr create",
    "env gh pr create",
    "env -u GH_HOST GH_TOKEN=x gh --repo o/r pr create",
    "if git push; then echo ok; fi",
    'echo "$(git push)"',
    "git commit -m 'x' && git push",
]

NOT_GATED = [
    'git commit -m "then git push"',
    # A separator inside a quoted argument does not start a command.
    'git commit -m "docs: explain workflow; git push after checks"',
    "git commit -m 'wip; git push origin main'",
    "printf '%s\\n' 'a && gh pr create'",
    "cat <<'EOF' > notes.md\ngit push\nEOF",
    "echo git push",
    "grep -rn 'gh pr create' .",
    "git log --oneline",
    "mygit push",
    "git -C push log",
    "gh pr view 3",
]


@pytest.mark.parametrize("command", GATED)
def test_a_failing_check_blocks_the_call(
    project: Path, tmp_path: Path, command: str
) -> None:
    result = run_gate(project, tmp_path, bash(command), exit_code=1)
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    assert "ruff FAILED" in output["permissionDecisionReason"]
    assert "E501 line too long" in output["permissionDecisionReason"]
    assert not (tmp_path / "state" / f"{SESSION}.json").exists(), (
        "a blocked push must not arm the completion check"
    )


def test_the_mcp_pull_request_tool_is_gated(project: Path, tmp_path: Path) -> None:
    call = {"tool_name": "mcp__github__create_pull_request", "tool_input": {}}
    result = run_gate(project, tmp_path, call, exit_code=1)
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == (
        "deny"
    )


@pytest.mark.parametrize("command", NOT_GATED)
def test_other_commands_run_no_check(
    project: Path, tmp_path: Path, command: str
) -> None:
    result = run_gate(project, tmp_path, bash(command), exit_code=1)
    assert result.stdout == ""
    assert not (tmp_path / "ruff.log").exists(), "the checks must not run"


def test_a_passing_push_runs_the_checks_and_records_the_push(
    project: Path, tmp_path: Path
) -> None:
    result = run_gate(project, tmp_path, bash("git -C /a push"), exit_code=0)
    assert result.stdout == ""
    assert (tmp_path / "ruff.log").read_text(encoding="utf-8") == "called\n"
    saved = json.loads((tmp_path / "state" / f"{SESSION}.json").read_text("utf-8"))
    assert isinstance(saved["pushedAt"], int) and saved["pushedAt"] > 0


def test_a_passing_pr_create_records_no_push(project: Path, tmp_path: Path) -> None:
    result = run_gate(project, tmp_path, bash("gh pr create"), exit_code=0)
    assert result.stdout == ""
    assert (tmp_path / "ruff.log").exists()
    assert not (tmp_path / "state" / f"{SESSION}.json").exists()


def test_the_wired_command_blocks_through_safe_launch(
    project: Path, tmp_path: Path
) -> None:
    """The settings.json line itself, so a matcher or launch line that never
    reaches the gate fails here."""
    hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    wired = [
        (group["matcher"], entry["command"])
        for group in hooks
        for entry in group["hooks"]
        if "push-gate.mjs" in entry["command"]
    ]
    assert len(wired) == 1, wired
    matcher, command = wired[0]
    assert matcher == "Bash|mcp__.*create_pull_request"
    result = run_gate(
        project, tmp_path, bash("git -C /a push"), 1, command=["bash", "-c", command]
    )
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == (
        "deny"
    )


def test_unparsable_stdin_passes(project: Path, tmp_path: Path) -> None:
    result = subprocess.run(
        ["node", str(project / ".claude" / "hooks" / "push-gate.mjs")],
        input="not json",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_shortlisted_command_counts_when_the_parser_is_missing(
    project: Path, tmp_path: Path
) -> None:
    """With no node_modules the call cannot be judged, so a command that names
    `git` and `push` runs the checks. One that names neither is not parsed."""
    (project / "node_modules").unlink()
    command = 'git commit -m "docs: explain workflow; git push after checks"'
    result = run_gate(project, tmp_path, bash(command), exit_code=1)
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == (
        "deny"
    )
    assert run_gate(project, tmp_path, bash("ls -la"), exit_code=1).stdout == ""
