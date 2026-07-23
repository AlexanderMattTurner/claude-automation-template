"""End-to-end tests for .claude/hooks/drop-superseded-ci-events.mjs.

Each test drives the real hook as a subprocess against a real local git remote
(a bare repo reached over file://, so no network) and asserts the observable
outcome: the block JSON on stdout for a superseded SHA, or silence (pass-through)
for a live head, a non-CI prompt, or any failure the hook must fail OPEN on.

The hook crosses the agent boundary through the agent-control-plane-core package
(installed by session-setup.sh / setup-base-env). The block path therefore needs
that package resolvable from the repo's node_modules; every pass-through path is
correct whether or not it loads (an unavailable package fails open to silence),
and one test exercises exactly that fail-open path with the package absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
HOOK = HOOKS_DIR / "drop-superseded-ci-events.mjs"
HOOK_LIBS = ("lib-hook-io.mjs", "lib-control-plane.mjs")

DEAD_SHA = "a" * 40  # 40 hex chars that head no branch


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _project_with_remote(tmp_path: Path) -> tuple[Path, str]:
    """A working repo whose `origin` is a local bare repo with one pushed
    branch. Returns (project_dir, head_sha of the pushed branch)."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    project = tmp_path / "project"
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "config", "user.email", "t@t.t")
    _git(project, "config", "user.name", "t")
    (project / "f.txt").write_text("x\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "init")
    _git(project, "remote", "add", "origin", bare.as_uri())
    _git(project, "push", "-q", "origin", "HEAD:main")
    head = _git(project, "rev-parse", "HEAD")
    return project, head


def _ci_prompt(sha: str, conclusion: str = "failure") -> str:
    return (
        "<github-webhook-activity>\n"
        "Event: check_run\n"
        f"Conclusion: {conclusion}\n"
        f"HeadSHA: {sha}\n"
        "</github-webhook-activity>"
    )


def _run_hook(
    prompt: str, project: Path, hook: Path = HOOK
) -> subprocess.CompletedProcess:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "session_id": "sess",
        "cwd": str(project),
    }
    return subprocess.run(
        ["node", str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "CLAUDE_PROJECT_DIR": str(project),
        },
    )


def test_blocks_superseded_sha(tmp_path: Path) -> None:
    """A red event on a SHA that heads no remote branch is blocked, with the
    block verdict rendered by the control-plane package (decision=block)."""
    project, _ = _project_with_remote(tmp_path)
    result = _run_hook(_ci_prompt(DEAD_SHA), project)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert DEAD_SHA[:12] in body["reason"]


def test_passes_current_head(tmp_path: Path) -> None:
    project, head = _project_with_remote(tmp_path)
    result = _run_hook(_ci_prompt(head), project)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_passes_success_conclusion(tmp_path: Path) -> None:
    """A green/non-red event is never dropped even on a superseded SHA."""
    project, _ = _project_with_remote(tmp_path)
    result = _run_hook(_ci_prompt(DEAD_SHA, conclusion="success"), project)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_passes_non_ci_prompt(tmp_path: Path) -> None:
    project, _ = _project_with_remote(tmp_path)
    result = _run_hook("please refactor the parser", project)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_passes_when_no_remote(tmp_path: Path) -> None:
    """git ls-remote failing (no origin) must fail OPEN, not block."""
    project = tmp_path / "loner"
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    result = _run_hook(_ci_prompt(DEAD_SHA), project)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_fails_open_on_malformed_stdin(tmp_path: Path) -> None:
    project, _ = _project_with_remote(tmp_path)
    result = subprocess.run(
        ["node", str(HOOK)],
        input="not json {",
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "CLAUDE_PROJECT_DIR": str(project),
        },
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_ignores_non_userpromptsubmit_event(tmp_path: Path) -> None:
    project, _ = _project_with_remote(tmp_path)
    payload = {
        "hook_event_name": "PostToolUse",
        "prompt": _ci_prompt(DEAD_SHA),
        "session_id": "sess",
    }
    result = subprocess.run(
        ["node", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "CLAUDE_PROJECT_DIR": str(project),
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_fails_open_when_control_plane_absent(tmp_path: Path) -> None:
    """With agent-control-plane-core unresolvable (fresh clone / cold start
    before install), the caught import leaves the bindings undefined, so even a
    genuinely-superseded event passes through untouched rather than blocking
    blind. Isolate by running copies of the hook + libs from a tree with no
    node_modules anywhere above them."""
    project, _ = _project_with_remote(tmp_path)
    isolated_hooks = tmp_path / "iso" / ".claude" / "hooks"
    isolated_hooks.mkdir(parents=True)
    for name in (HOOK.name, *HOOK_LIBS):
        shutil.copy(HOOKS_DIR / name, isolated_hooks / name)
    result = _run_hook(_ci_prompt(DEAD_SHA), project, hook=isolated_hooks / HOOK.name)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "agent-control-plane-core is unavailable" in result.stderr
