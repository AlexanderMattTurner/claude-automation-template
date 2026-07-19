"""Smoke tests for .claude/hooks/ scripts."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_SETUP = REPO_ROOT / ".claude" / "hooks" / "session-setup.sh"
SAFE_LAUNCH_PARSE = REPO_ROOT / ".claude" / "hooks" / "safe-launch-parse.py"
SAFE_LAUNCH = REPO_ROOT / ".claude" / "hooks" / "safe-launch.sh"
PRE_PUSH_CHECK = REPO_ROOT / ".claude" / "hooks" / "pre-push-check.sh"
LIB_CHECKS = REPO_ROOT / ".claude" / "hooks" / "lib-checks.sh"


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
    ["[1, 2, 3]", "null", "42", '"hello"'],
    ids=["array", "null", "number", "string"],
)
def test_safe_launch_parse_valid_non_dict_json_exits_zero(stdin: str) -> None:
    """Syntactically valid JSON that isn't an object (list/null/number/string)
    must not crash `.get()`; it must fall through to the fail-safe "ask"
    default the same as malformed JSON."""
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
            # Isolate from the host's global/system git config: a
            # url.insteadOf rewrite (e.g. a Claude web session's repo proxy)
            # would rewrite the remote URL a test just set, flipping the
            # GH_REPO extraction expectations. Global writes land in a
            # sandbox-local file so the run stays hermetic both ways.
            "GIT_CONFIG_GLOBAL": str(sandbox / "gitconfig-global"),
            "GIT_CONFIG_SYSTEM": os.devnull,
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


@pytest.fixture
def safe_launch_sandbox(tmp_path: Path) -> Path:
    """Project dir containing a copy of safe-launch.sh under .claude/hooks/,
    plus a deliberately-broken target hook so the degraded path is exercised."""
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    launcher = hooks_dir / "safe-launch.sh"
    launcher.write_bytes(SAFE_LAUNCH.read_bytes())
    launcher.chmod(0o755)
    parser = hooks_dir / "safe-launch-parse.py"
    parser.write_bytes(SAFE_LAUNCH_PARSE.read_bytes())
    broken = hooks_dir / "broken-hook.sh"
    broken.write_text("if true; then\n  echo unterminated\n")  # missing `fi`
    return tmp_path


def _run_safe_launch(project_dir: Path, payload: dict) -> subprocess.CompletedProcess:
    launcher = project_dir / ".claude" / "hooks" / "safe-launch.sh"
    target = project_dir / ".claude" / "hooks" / "broken-hook.sh"
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        ["bash", str(launcher), str(target)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def test_safe_launch_degraded_path_blocks_symlink_escape(
    safe_launch_sandbox: Path,
) -> None:
    """A symlink inside .claude/hooks/ that points outside the project dir
    must not be treated as a legitimate self-repair target: its lexical path
    is under the safe dir, but it resolves elsewhere. Must fall through to
    the fail-safe "ask" default rather than silently allowing the edit."""
    outside_target = safe_launch_sandbox.parent / "outside-secret.sh"
    outside_target.write_text("echo not a hook\n")
    escape_link = safe_launch_sandbox / ".claude" / "hooks" / "escape-link.sh"
    escape_link.symlink_to(outside_target)

    result = _run_safe_launch(
        safe_launch_sandbox,
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(escape_link)},
        },
    )
    assert result.returncode == 0, result.stderr
    assert '"permissionDecision":"ask"' in result.stdout
    assert "allowing self-repair edit" not in result.stderr


def test_safe_launch_degraded_path_allows_real_self_repair(
    safe_launch_sandbox: Path,
) -> None:
    """A genuine (non-symlink) edit target under .claude/hooks/ must still be
    allowed through, so self-repair of a broken hook keeps working."""
    real_target = safe_launch_sandbox / ".claude" / "hooks" / "broken-hook.sh"
    result = _run_safe_launch(
        safe_launch_sandbox,
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(real_target)},
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert "allowing self-repair edit" in result.stderr


@pytest.fixture
def pre_push_sandbox(tmp_path: Path) -> Path:
    """Project dir containing a copy of pre-push-check.sh and lib-checks.sh."""
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    for src in (PRE_PUSH_CHECK, LIB_CHECKS):
        dst = hooks_dir / src.name
        dst.write_bytes(src.read_bytes())
        dst.chmod(0o755)
    return tmp_path


def _minimal_path(tmp_path: Path) -> str:
    """A PATH exposing only `dirname` (needed by pre-push-check.sh itself),
    so jq/ruff/uv are guaranteed absent regardless of what's installed on
    the host."""
    bin_dir = tmp_path / "minimal-bin"
    bin_dir.mkdir(exist_ok=True)
    found = shutil.which("dirname")
    assert found
    (bin_dir / "dirname").symlink_to(found)
    return str(bin_dir)


def _run_pre_push_check(project_dir: Path, path: str) -> subprocess.CompletedProcess:
    launcher = project_dir / ".claude" / "hooks" / "pre-push-check.sh"
    bash = shutil.which("bash")
    assert bash
    env = dict(os.environ)
    env["PATH"] = path
    return subprocess.run(
        [bash, str(launcher)],
        cwd=project_dir,
        capture_output=True,
        text=True,
        env=env,
    )


def test_pre_push_check_fails_loudly_when_jq_missing(
    pre_push_sandbox: Path,
) -> None:
    """A configured package.json with no `jq` on PATH must fail the check,
    not silently skip all Node checks (there's no way to tell what's
    configured without jq)."""
    (pre_push_sandbox / "package.json").write_text('{"scripts": {}}')
    result = _run_pre_push_check(pre_push_sandbox, _minimal_path(pre_push_sandbox))
    assert result.returncode == 1
    assert "jq is required" in result.stderr


def test_pre_push_check_fails_loudly_when_ruff_and_uv_missing(
    pre_push_sandbox: Path,
) -> None:
    """A Python project with neither `ruff` nor `uv` on PATH must fail the
    check, not silently skip the ruff check."""
    (pre_push_sandbox / "pyproject.toml").write_text("")
    result = _run_pre_push_check(pre_push_sandbox, _minimal_path(pre_push_sandbox))
    assert result.returncode == 1
    assert "Neither ruff nor uv" in result.stderr


def test_pre_push_check_runs_ruff_when_available(
    pre_push_sandbox: Path,
) -> None:
    """A Python project with `ruff` on PATH must actually invoke it with the
    exact expected argv (catches quoting/word-splitting regressions in the
    argv-based run_check)."""
    (pre_push_sandbox / "pyproject.toml").write_text("")
    path = _minimal_path(pre_push_sandbox)
    bin_dir = Path(path)
    marker = pre_push_sandbox / "ruff-invoked-with-args.txt"
    fake_ruff = bin_dir / "ruff"
    fake_ruff.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{marker}"\n')
    fake_ruff.chmod(0o755)
    result = _run_pre_push_check(pre_push_sandbox, path)
    assert result.returncode == 0, result.stderr
    assert marker.read_text().splitlines() == ["check", "."]


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
