"""Smoke tests for .claude/hooks/ scripts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_SETUP = REPO_ROOT / ".claude" / "hooks" / "session-setup.sh"
SAFE_LAUNCH_PARSE = REPO_ROOT / ".claude" / "hooks" / "safe-launch-parse.py"


def _run_parser_raw(
    stdin: str, project_dir: str = "/project"
) -> subprocess.CompletedProcess:
    """Run safe-launch-parse.py with raw *stdin* bytes; return the completed process."""
    return subprocess.run(
        [sys.executable, str(SAFE_LAUNCH_PARSE), project_dir],
        input=stdin,
        capture_output=True,
        text=True,
    )


def _run_parser(payload: dict, project_dir: str = "/project") -> tuple[str, str]:
    """Run safe-launch-parse.py with *payload* on stdin; return (tool_name, path)."""
    result = _run_parser_raw(json.dumps(payload), project_dir)
    lines = result.stdout.splitlines()
    tool_name = lines[0] if len(lines) > 0 else ""
    path = lines[1] if len(lines) > 1 else ""
    return tool_name, path


@pytest.mark.parametrize(
    "payload, expected_tool, expected_path_suffix",
    [
        # Edit: file_path in tool_input
        (
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/project/.claude/hooks/safe-launch.sh"},
            },
            "Edit",
            ".claude/hooks/safe-launch.sh",
        ),
        # Write: file_path in tool_input
        (
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/project/.hooks/pre-commit"},
            },
            "Write",
            ".hooks/pre-commit",
        ),
        # MultiEdit: path lives inside edits[0].file_path, NOT tool_input.file_path
        (
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "edits": [
                        {
                            "file_path": "/project/.claude/hooks/session-setup.sh",
                            "old_string": "x",
                            "new_string": "y",
                        },
                        {
                            "file_path": "/project/other.sh",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    ]
                },
            },
            "MultiEdit",
            ".claude/hooks/session-setup.sh",
        ),
        # MultiEdit with empty edits array → empty path (safe default)
        (
            {"tool_name": "MultiEdit", "tool_input": {"edits": []}},
            "MultiEdit",
            "",
        ),
        # Bash: no file path
        (
            {"tool_name": "Bash", "tool_input": {"command": "git push"}},
            "Bash",
            "",
        ),
        # Empty but valid JSON object → empty tool name and path
        (
            {},
            "",
            "",
        ),
    ],
    ids=["Edit", "Write", "MultiEdit", "MultiEdit-empty", "Bash", "empty-payload"],
)
def test_safe_launch_parse(
    payload: dict, expected_tool: str, expected_path_suffix: str
) -> None:
    tool_name, path = _run_parser(payload)
    assert tool_name == expected_tool
    if expected_path_suffix:
        assert path.endswith(expected_path_suffix), (
            f"path={path!r} expected suffix {expected_path_suffix!r}"
        )
    else:
        assert path == ""


@pytest.mark.parametrize(
    "stdin",
    ["not json at all", "", "{unterminated", "[1, 2, 3"],
    ids=["text", "empty", "brace", "array"],
)
def test_safe_launch_parse_malformed_json_exits_zero(stdin: str) -> None:
    """Non-JSON stdin must exit 0 with empty output so safe-launch.sh falls
    through to its fail-safe "ask" default rather than erroring."""
    result = _run_parser_raw(stdin)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    "stdin",
    ["null", '"a string"', "[1, 2, 3]", "42", "true"],
    ids=["null", "string", "complete-array", "number", "bool"],
)
def test_safe_launch_parse_non_object_json_exits_zero(stdin: str) -> None:
    """Syntactically complete JSON that isn't an object (a list, string,
    number, or bool all parse successfully) must not crash with an
    AttributeError from calling .get() on a non-dict — it must degrade to
    the same empty-output fail-safe as unparseable input."""
    result = _run_parser_raw(stdin)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Throwaway git repo containing a copy of session-setup.sh under
    .claude/hooks/. The script computes its project dir from $(dirname $0)/../..
    rather than $CLAUDE_PROJECT_DIR, so the script must live inside the
    sandbox for tests to operate on it."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    script = hooks_dir / "session-setup.sh"
    script.write_bytes(SESSION_SETUP.read_bytes())
    script.chmod(0o755)
    return tmp_path


def set_remote(sandbox: Path, url: str) -> None:
    subprocess.run(["git", "remote", "add", "origin", url], cwd=sandbox, check=True)


def run_session_setup(
    sandbox: Path,
    *,
    extra_env: dict[str, str] | None = None,
    scrub: tuple[str, ...] = (),
) -> tuple[Path, subprocess.CompletedProcess]:
    """Invoke session-setup.sh in the sandbox; return (env_file, result)."""
    env = {k: v for k, v in os.environ.items() if k not in scrub}
    env_file = sandbox / "claude.env"
    env_file.touch()
    env.update(
        {
            "CLAUDE_PROJECT_DIR": str(sandbox),
            "CLAUDE_ENV_FILE": str(env_file),
            "GH_TOKEN": "fake",
        }
    )
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(sandbox / ".claude" / "hooks" / "session-setup.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    return env_file, result


@pytest.mark.parametrize(
    "remote_url, expected",
    [
        (
            "http://local_proxy@127.0.0.1:18393/git/test-owner/test-repo",
            "test-owner/test-repo",
        ),
        (
            "http://local_proxy@127.0.0.1:18393/git/owner/repo.git",
            "owner/repo",
        ),
        ("https://github.com/owner/repo.git", None),
        ("https://evil.com/notgit/owner/repo", None),
        ("git@github.com:owner/repo.git", None),
    ],
    ids=["proxy", "proxy-with-.git", "github-https", "hostile-substring", "ssh"],
)
def test_gh_repo_extraction(
    sandbox: Path, remote_url: str, expected: str | None
) -> None:
    set_remote(sandbox, remote_url)
    env_file, result = run_session_setup(
        sandbox, scrub=("GH_REPO", "CLAUDE_CODE_BASE_REF")
    )
    assert result.returncode == 0, (
        f"session-setup.sh exited {result.returncode}\nstderr: {result.stderr}"
    )
    exports = [
        line
        for line in env_file.read_text().splitlines()
        if line.startswith("export GH_REPO=")
    ]
    if expected is None:
        assert exports == [], f"expected no GH_REPO export, got: {exports}"
    else:
        assert len(exports) == 1, f"expected exactly one GH_REPO export, got: {exports}"
        sourced = subprocess.run(
            ["bash", "-c", f"{exports[0]}; printf '%s' \"$GH_REPO\""],
            capture_output=True,
            text=True,
            check=True,
        )
        assert sourced.stdout == expected


def test_preserves_pre_set_gh_repo(sandbox: Path) -> None:
    """Pre-existing $GH_REPO must not be overwritten by extraction."""
    set_remote(sandbox, "http://local_proxy@127.0.0.1:18393/git/other-owner/other-repo")
    env_file, result = run_session_setup(
        sandbox,
        extra_env={"GH_REPO": "preset/value"},
        scrub=("CLAUDE_CODE_BASE_REF",),
    )
    assert result.returncode == 0, result.stderr
    exports = [
        line
        for line in env_file.read_text().splitlines()
        if line.startswith("export GH_REPO=")
    ]
    assert exports == []


@pytest.mark.parametrize(
    "remote_url, expect_settings",
    [
        ("http://local_proxy@127.0.0.1:18393/git/owner/repo", True),
        ("http://local_proxy@127.0.0.1:18393/git/owner/repo.git", True),
        # Substring-only matches: contain "127.0.0.1" and "/git/" somewhere in
        # the URL, but the host authority isn't actually 127.0.0.1 — the
        # unanchored regex this replaces would have matched these too.
        ("https://attacker.example/redirect?to=127.0.0.1/git/", False),
        ("https://attacker.example/127.0.0.1-fake/x/git/", False),
        ("https://github.com/owner/repo.git", False),
    ],
    ids=["proxy", "proxy-with-.git", "hostile-query", "hostile-path", "github-https"],
)
def test_web_session_permissions_grant_requires_real_proxy_host(
    sandbox: Path, remote_url: str, expect_settings: bool
) -> None:
    """settings.local.json (which grants broad Edit/Write/Bash auto-approval)
    must only be written for the actual local-proxy remote shape, not for any
    URL that merely contains "127.0.0.1" and "/git/" as substrings."""
    set_remote(sandbox, remote_url)
    _, result = run_session_setup(sandbox, scrub=("GH_REPO", "CLAUDE_CODE_BASE_REF"))
    assert result.returncode == 0, result.stderr
    local_settings = sandbox / ".claude" / "settings.local.json"
    assert local_settings.is_file() == expect_settings
