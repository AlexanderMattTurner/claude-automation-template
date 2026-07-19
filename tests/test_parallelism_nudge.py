"""End-to-end tests for .claude/hooks/parallelism-nudge.mjs.

Each test drives the real hook as a subprocess with a real transcript file on
disk and asserts the observable outcome: the hookSpecificOutput JSON on stdout
(or silence), the exit code, and the once-per-segment sentinel behavior.
"""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "parallelism-nudge.mjs"

SERIAL_TOOL_TURN_THRESHOLD = 15


def _prompt_line(uuid: str = "u1") -> str:
    return json.dumps(
        {"type": "user", "uuid": uuid, "message": {"role": "user", "content": "go"}}
    )


def _tool_line(msg_id: str, name: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": f"a-{msg_id}",
            "message": {
                "role": "assistant",
                "id": msg_id,
                "content": [{"type": "tool_use", "name": name, "input": {}}],
            },
        }
    )


def _serial_transcript(turns: int, extra_lines: list[str] | None = None) -> str:
    lines = [_prompt_line("seg")]
    lines += [_tool_line(f"m{i}", "Bash") for i in range(turns)]
    lines += extra_lines or []
    return "\n".join(lines) + "\n"


def _run_hook(transcript: Path, tmp_dir: Path, session: str = "sess") -> subprocess.CompletedProcess:
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "tool_response": "ok",
        "session_id": session,
        "transcript_path": str(transcript),
    }
    return subprocess.run(
        ["node", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TMPDIR": str(tmp_dir)},
    )


def test_nudges_on_serial_streak_then_sentinel_silences(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_serial_transcript(SERIAL_TOOL_TURN_THRESHOLD))
    first = _run_hook(transcript, tmp_path)
    assert first.returncode == 0
    body = json.loads(first.stdout)["hookSpecificOutput"]
    assert body["hookEventName"] == "PostToolUse"
    assert f"{SERIAL_TOOL_TURN_THRESHOLD} tool-calling turns" in body["additionalContext"]
    second = _run_hook(transcript, tmp_path)
    assert second.returncode == 0
    assert second.stdout == ""


def test_silent_below_threshold(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_serial_transcript(SERIAL_TOOL_TURN_THRESHOLD - 1))
    result = _run_hook(transcript, tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_delegation_in_segment_silences(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        _serial_transcript(
            SERIAL_TOOL_TURN_THRESHOLD, extra_lines=[_tool_line("mT", "Task")]
        )
    )
    result = _run_hook(transcript, tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_fails_open_on_missing_transcript(tmp_path: Path) -> None:
    result = _run_hook(tmp_path / "missing.jsonl", tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_fails_open_on_malformed_stdin(tmp_path: Path) -> None:
    result = subprocess.run(
        ["node", str(HOOK)],
        input="not json {",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TMPDIR": str(tmp_path)},
    )
    assert result.returncode == 0
    assert result.stdout == ""
