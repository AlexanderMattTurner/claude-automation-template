"""End-to-end tests for .claude/hooks/completion-check.mjs.

Drives the two commands `.claude/settings.json` wires — the PreToolUse
`--record-push` arm through `safe-launch.sh`, and the Stop arm through `node` —
as subprocesses against a real transcript file, and asserts what Claude Code
would see: the block JSON on stdout, or silence. The in-process cases live in
tests/completion-check.test.mjs; only this can tell a hook that works in
isolation from one whose launch line never fires it.
"""

import json
import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SETTINGS = REPO_ROOT / ".claude" / "settings.json"
SESSION = "wired-completion"


def _wired_command(event: str) -> str:
    """The one command settings.json launches for this hook on `event`."""
    hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]
    commands = [
        entry["command"]
        for group in hooks.get(event, [])
        for entry in group.get("hooks", [])
        if "completion-check.mjs" in entry.get("command", "")
    ]
    assert len(commands) == 1, (
        f"settings.json wires the hook {len(commands)} times on {event} — a wiring "
        f"scan that finds none makes every assertion below pass over nothing"
    )
    return commands[0]


def test_the_push_arm_is_gated_on_the_harness_push_matcher():
    """The hook never parses shell text; settings.json's `if` is what decides a
    Bash call is a push, and it is the matcher pre-push-check.sh already uses."""
    hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]
    entries = [
        entry
        for group in hooks["PreToolUse"]
        for entry in group.get("hooks", [])
        if "completion-check.mjs" in entry.get("command", "")
    ]
    assert [e.get("if") for e in entries] == ["Bash(git push:*)"]


def _run(event: str, payload: dict, state: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", _wired_command(event)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env={
            "PATH": os.environ["PATH"],
            "CLAUDE_PROJECT_DIR": str(REPO_ROOT),
            "COMPLETION_CHECK_STATE_DIR": str(state),
        },
        timeout=60,
    )


def _line(role: str, content: list) -> str:
    return json.dumps({"message": {"role": role, "content": content}})


def _worked_turn(text: str) -> str:
    return "\n".join(
        [
            _line("user", [{"type": "text", "text": "do it"}]),
            _line(
                "assistant",
                [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}],
            ),
            _line(
                "user", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]
            ),
            _line("assistant", [{"type": "text", "text": text}]),
        ]
    )


def test_the_wired_push_arm_records_the_push_and_says_nothing(tmp_path):
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": "git push -u origin x"},
    }
    proc = _run("PreToolUse", payload, tmp_path)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[:400]
    assert proc.stdout.decode().strip() == ""
    saved = json.loads((tmp_path / f"{SESSION}.json").read_text(encoding="utf-8"))
    assert isinstance(saved["pushedAt"], int) and saved["pushedAt"] > 0


def test_the_wired_stop_arm_allows_a_session_that_never_pushed(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_worked_turn("Done."), encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": SESSION,
        "transcript_path": str(transcript),
    }
    proc = _run("Stop", payload, tmp_path)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[:400]
    assert proc.stdout.decode().strip() == ""


def test_the_wired_stop_arm_asks_once_the_window_has_passed(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_worked_turn("Pushed the fix."), encoding="utf-8")
    # Pushed long ago, window behind, stop count spent: the next stop asks.
    (tmp_path / f"{SESSION}.json").write_text(
        json.dumps(
            {"pushedAt": 1, "deadlineMs": 2, "stopsLeft": 1, "pings": 0, "done": False}
        ),
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "Stop",
        "session_id": SESSION,
        "transcript_path": str(transcript),
    }

    first = _run("Stop", payload, tmp_path)
    assert first.returncode == 0, first.stderr.decode(errors="replace")[:400]
    emitted = json.loads(first.stdout)
    assert emitted["decision"] == "block"
    assert "Completion check 1 of 3" in emitted["reason"]

    transcript.write_text(_worked_turn("All done.\n\nYes."), encoding="utf-8")
    second = _run("Stop", payload, tmp_path)
    assert second.returncode == 0
    assert second.stdout.decode().strip() == ""
    saved = json.loads((tmp_path / f"{SESSION}.json").read_text(encoding="utf-8"))
    assert saved["done"] is True
