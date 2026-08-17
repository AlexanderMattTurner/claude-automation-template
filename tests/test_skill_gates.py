"""Behavioral tests for the PreToolUse skill gates.

Each gate denies a tool call until the Skill tool was invoked for its skill, and
allows it afterwards. Both halves matter: a gate that never denies is inert, and
one that never clears is a wedge with no remedy. The cases below drive the real
hook as a subprocess over its stdin protocol, so a break in the wiring — the
judge order, the deny JSON, the marker write — fails here rather than in a
session.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT, run_capture

HOOK = REPO_ROOT / ".claude" / "hooks" / "skill-gates.mjs"
SESSION = "sess-1"


def run_hook(payload: dict, gate_dir: Path) -> dict | None:
    """Drive the hook over stdin; return its parsed response, or None for allow."""
    env = {**os.environ, "CLAUDE_SKILL_GATE_DIR": str(gate_dir)}
    result = run_capture(
        ["node", str(HOOK)], input=json.dumps(payload), env=env, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def deny_reason(response: dict | None) -> str:
    assert response is not None, "expected a deny, got an allow"
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    return output["permissionDecisionReason"]


def invoke_skill(skill: str) -> dict:
    return {
        "session_id": SESSION,
        "tool_name": "Skill",
        "tool_input": {"skill": skill},
    }


# (label, the gated payload, the skill that clears it)
GATED_CALLS = [
    (
        "gh pr create",
        {"tool_name": "Bash", "tool_input": {"command": "gh pr create --fill"}},
        "pr-creation",
    ),
    (
        "the MCP create_pull_request tool",
        {"tool_name": "mcp__github__create_pull_request", "tool_input": {}},
        "pr-creation",
    ),
    (
        "writing a pytest module",
        {"tool_name": "Write", "tool_input": {"file_path": "tests/test_thing.py"}},
        "writing-tests",
    ),
    (
        "editing a node test module",
        {"tool_name": "Edit", "tool_input": {"file_path": "a/b/thing.test.mjs"}},
        "writing-tests",
    ),
    (
        "writing a plan file",
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/claude/plan.md"}},
        "explore-plan",
    ),
    (
        "gh issue create naming a plan",
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'gh issue create --title "Repo migration plan" --body "..."'
            },
        },
        "explore-plan",
    ),
    (
        "the MCP issue_write create tool naming a plan",
        {
            "tool_name": "mcp__github__issue_write",
            "tool_input": {"method": "create", "title": "Repo migration plan"},
        },
        "explore-plan",
    ),
]


@pytest.mark.parametrize("label, call, skill", GATED_CALLS, ids=lambda v: str(v)[:40])
def test_a_gated_call_is_denied_until_its_skill_runs(
    tmp_path: Path, label: str, call: dict, skill: str
) -> None:
    payload = {"session_id": SESSION, **call}
    reason = deny_reason(run_hook(payload, tmp_path))
    assert skill in reason, f"{label}: the deny must name the skill to invoke"

    assert run_hook(invoke_skill(skill), tmp_path) is None
    assert run_hook(payload, tmp_path) is None, (
        f"{label}: the gate must clear once its skill ran"
    )


def test_one_gates_skill_does_not_satisfy_another(tmp_path: Path) -> None:
    """The markers are per skill. Sharing one would let any skill invocation open
    every gate, which is the whole guarantee gone."""
    assert run_hook(invoke_skill("writing-tests"), tmp_path) is None
    payload = {
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr create --fill"},
    }
    assert "pr-creation" in deny_reason(run_hook(payload, tmp_path))


def test_a_marker_from_another_session_does_not_clear_this_one(
    tmp_path: Path,
) -> None:
    other = {**invoke_skill("pr-creation"), "session_id": "other"}
    assert run_hook(other, tmp_path) is None
    payload = {
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr create --fill"},
    }
    assert "pr-creation" in deny_reason(run_hook(payload, tmp_path))


ALLOWED_CALLS = [
    # The words appear, but inside another program's argument — nothing is created.
    (
        "a quoted mention of the command",
        {
            "tool_name": "Bash",
            "tool_input": {"command": 'git commit -m "explain gh pr create"'},
        },
    ),
    (
        "a similarly named binary",
        {"tool_name": "Bash", "tool_input": {"command": "mygh pr create"}},
    ),
    (
        "a non-test source file",
        {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
    ),
    # `pretest_x.py` contains `test_x.py`, so an unanchored pattern fires on it.
    (
        "a file whose name merely contains a test name",
        {"tool_name": "Write", "tool_input": {"file_path": "pretest_thing.py"}},
    ),
    # `explanation` contains `plan`; firing here teaches the session the gate is noise.
    (
        "a doc whose name merely contains 'plan'",
        {"tool_name": "Write", "tool_input": {"file_path": "docs/explanation.md"}},
    ),
    (
        "reading a test file",
        {"tool_name": "Read", "tool_input": {"file_path": "tests/test_thing.py"}},
    ),
    # The gate watches `create` only: an issue that exists already passed it.
    (
        "an issue update naming a plan",
        {
            "tool_name": "mcp__github__issue_write",
            "tool_input": {"method": "update", "title": "Repo migration plan"},
        },
    ),
    (
        "an issue create whose title doesn't name a plan",
        {
            "tool_name": "mcp__github__issue_write",
            "tool_input": {"method": "create", "title": "Track the migration"},
        },
    ),
]


@pytest.mark.parametrize("label, call", ALLOWED_CALLS, ids=lambda v: str(v)[:40])
def test_an_untriggered_call_passes(tmp_path: Path, label: str, call: dict) -> None:
    payload = {"session_id": SESSION, **call}
    assert run_hook(payload, tmp_path) is None, f"{label}: must not be gated"


def test_an_unusable_session_id_never_wedges_the_session(tmp_path: Path) -> None:
    """No session key means no way to record that the skill ran, so a deny could
    never be cleared. The gate passes instead — a missed reminder beats a session
    with no way forward."""
    payload = {
        "session_id": "../escape",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr create --fill"},
    }
    assert run_hook(payload, tmp_path) is None


def test_unparsable_stdin_fails_open(tmp_path: Path) -> None:
    """A PreToolUse hook that produces no response is non-blocking, so a crash
    would allow the call with no message at all. Exit cleanly instead."""
    result = subprocess.run(
        ["node", str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_SKILL_GATE_DIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
