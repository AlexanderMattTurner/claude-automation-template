"""End-to-end tests for .claude/hooks/bullshit-check.mjs.

Each test drives the real hook as a subprocess through the command
`.claude/settings.json` wires, under each event that command is wired to, and
asserts the observable outcome: the additionalContext JSON on stdout carrying
the event's own name, or silence, plus the state file the run leaves behind. The
in-process cases live in tests/bullshit-check.test.mjs; only this can tell a
hook that works in isolation from one whose launch line never fires it.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

HOOK = REPO_ROOT / ".claude" / "hooks" / "bullshit-check.mjs"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

# Every event the hook rides. Each one runs the agent already, so a question on
# it wakes nothing that was idle.
EVENTS = ["PostToolUse", "UserPromptSubmit"]

# Asks the hook's OWN exports which session to use and where its moments fall,
# so the arithmetic is never re-derived here. The id's segment 0 moment sits in
# the first half of the segment, which leaves a spawned hook slack to read its
# own clock, and its segment 2 moment sits at least ten seconds in, so a run can
# land before it. SEGMENT_MS divides the hash, so a hardcoded id's margins would
# move whenever that constant does.
_SESSION_JS = """
import { SEGMENT_MS, offsetMs } from "%s";
let n = 0;
while (offsetMs(`wired-session-${n}`, 0) >= SEGMENT_MS / 2
  || offsetMs(`wired-session-${n}`, 2) < 10_000) n += 1;
console.log(`wired-session-${n}`);
console.log(offsetMs(`wired-session-${n}`, 0));
console.log(SEGMENT_MS);
"""


@pytest.fixture(scope="module")
def session() -> tuple[str, int, int]:
    """(session id, its segment 0 offset in ms, SEGMENT_MS), from the hook."""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SESSION_JS % HOOK.as_uri()],
        capture_output=True,
        text=True,
        check=True,
    )
    name, offset, segment_ms = proc.stdout.split()
    return name, int(offset), int(segment_ms)


def _wired_command(event: str) -> str:
    """The one command settings.json launches for this hook on `event`."""
    hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]
    commands = [
        entry["command"]
        for group in hooks.get(event, [])
        for entry in group.get("hooks", [])
        if "bullshit-check.mjs" in entry.get("command", "")
    ]
    assert len(commands) == 1, (
        f"settings.json wires the hook {len(commands)} times on {event} — a wiring "
        f"scan that finds none makes every assertion below pass over nothing"
    )
    return commands[0]


def _run(
    event: str, session: tuple[str, int, int], state: Path
) -> subprocess.CompletedProcess:
    payload = {"hook_event_name": event, "session_id": session[0]}
    if event == "PostToolUse":
        payload["tool_name"] = "Bash"
    else:
        payload["prompt"] = "carry on"
    return subprocess.run(
        ["bash", "-c", _wired_command(event)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env={
            "PATH": os.environ["PATH"],
            "CLAUDE_PROJECT_DIR": str(REPO_ROOT),
            "BULLSHIT_CHECK_STATE_DIR": str(state),
        },
        timeout=30,
    )


def _record(state: Path, session: tuple[str, int, int]) -> Path:
    state.mkdir(exist_ok=True)
    return state / f"{session[0]}.segment"


def _now_ms() -> int:
    return int(time.time() * 1000)


@pytest.mark.parametrize("event", EVENTS)
def test_the_wired_command_delivers_the_question(event, tmp_path, session):
    # Anchored, nothing resolved yet, and the segment 0 moment five seconds
    # behind with the rest of the segment ahead.
    anchor = _now_ms() - session[1] - 5_000
    path = _record(tmp_path, session)
    path.write_text(f"{anchor} -1", encoding="utf-8")

    proc = _run(event, session, tmp_path)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[:400]
    emitted = json.loads(proc.stdout)["hookSpecificOutput"]
    # Claude Code ignores a body stamped with an event other than the one it fired.
    assert emitted["hookEventName"] == event
    assert "Bullshit check" in emitted["additionalContext"]
    assert path.read_text(encoding="utf-8") == f"{anchor} 0"


@pytest.mark.parametrize("event", EVENTS)
def test_the_wired_command_says_nothing_once_the_segment_is_spent(
    event, tmp_path, session
):
    anchor = _now_ms() - session[1] - 5_000
    _record(tmp_path, session).write_text(f"{anchor} 0", encoding="utf-8")

    proc = _run(event, session, tmp_path)

    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == ""


def test_an_overdue_check_rides_the_tool_call_and_not_the_prompt(tmp_path, session):
    """A session woken by prompts is overdue at every wake. Asking there would land
    the question at turn start, before any work exists to audit, so the prompt
    waits for the segment's own moment and the tool call takes the carry."""
    # Five seconds into segment 2, before its moment, with segment 1 never spent.
    anchor = _now_ms() - 2 * session[2] - 5_000
    path = _record(tmp_path, session)
    path.write_text(f"{anchor} 0", encoding="utf-8")

    prompt = _run("UserPromptSubmit", session, tmp_path)
    assert prompt.returncode == 0
    assert prompt.stdout.decode().strip() == ""
    assert path.read_text(encoding="utf-8") == f"{anchor} 0"

    tool = _run("PostToolUse", session, tmp_path)
    assert tool.returncode == 0, tool.stderr.decode(errors="replace")[:400]
    assert (
        "Bullshit check"
        in json.loads(tool.stdout)["hookSpecificOutput"]["additionalContext"]
    )
    assert path.read_text(encoding="utf-8") == f"{anchor} 2"


def test_the_wired_command_anchors_a_session_it_has_not_seen(tmp_path, session):
    path = _record(tmp_path, session)
    before = _now_ms()

    proc = _run("PostToolUse", session, tmp_path)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[:400]
    assert proc.stdout.decode().strip() == ""
    start, last = path.read_text(encoding="utf-8").split(" ")
    assert last == "-1"
    assert before <= int(start) <= _now_ms()
