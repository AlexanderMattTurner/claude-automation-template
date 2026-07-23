"""Tests for the git hooks in .hooks/ (pre-commit, commit-msg, pre-push).

The hooks are copied into a throwaway repo preserving the .hooks/ layout
(they source .hooks/lib-gate.sh relative to the repo root) and run with a
constrained PATH so "tool missing" scenarios are reproducible regardless of
what the host has installed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT, git_env, init_test_repo

ZERO_SHA = "0" * 40


@pytest.fixture
def hook_repo(tmp_path: Path) -> Path:
    """Throwaway git repo with the real .hooks/ scripts installed."""
    init_test_repo(tmp_path)
    hooks_dir = tmp_path / ".hooks"
    hooks_dir.mkdir()
    for name in ("pre-commit", "commit-msg", "pre-push", "lib-gate.sh"):
        dst = hooks_dir / name
        dst.write_bytes((REPO_ROOT / ".hooks" / name).read_bytes())
        dst.chmod(0o755)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        env=git_env(),
        check=True,
    )
    return tmp_path


def minimal_path(tmp_path: Path, *tools: str) -> str:
    """A PATH exposing only git plus *tools*, so pnpm/npm/npx/uvx/pre-commit
    are guaranteed absent regardless of what's installed on the host."""
    bin_dir = tmp_path / "minimal-bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("git", *tools):
        found = shutil.which(tool)
        assert found, f"{tool} not found on host PATH"
        (bin_dir / tool).symlink_to(found)
    return str(bin_dir)


def run_hook(
    repo: Path, name: str, *args: str, path: str, stdin: str = ""
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": path, "HOME": str(repo)}
    bash = shutil.which("bash")
    assert bash
    return subprocess.run(
        [bash, str(repo / ".hooks" / name), *args],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def test_pre_commit_passes_without_package_json(hook_repo: Path) -> None:
    """No package.json = not a Node project: the lint-staged gate is
    inapplicable and the commit goes through."""
    result = run_hook(hook_repo, "pre-commit", path=minimal_path(hook_repo))
    assert result.returncode == 0, result.stderr


def test_pre_commit_fails_when_lint_staged_missing(hook_repo: Path) -> None:
    """A Node project (package.json present) whose lint-staged is not
    installed must FAIL the commit, not silently skip all lint/format checks."""
    (hook_repo / "package.json").write_text('{"name": "x"}')
    result = run_hook(hook_repo, "pre-commit", path=minimal_path(hook_repo))
    assert result.returncode == 1
    assert "lint-staged" in result.stderr
    assert "REFUSING" in result.stderr


def test_pre_commit_fails_when_no_package_manager(hook_repo: Path) -> None:
    """lint-staged installed but neither pnpm nor npm on PATH must fail loud —
    this previously fell through an if/elif and exited 0 silently."""
    (hook_repo / "package.json").write_text('{"name": "x"}')
    fake_bin = hook_repo / "node_modules" / ".bin"
    fake_bin.mkdir(parents=True)
    (fake_bin / "lint-staged").write_text("#!/bin/bash\nexit 0\n")
    (fake_bin / "lint-staged").chmod(0o755)
    result = run_hook(hook_repo, "pre-commit", path=minimal_path(hook_repo))
    assert result.returncode == 1
    assert "pnpm" in result.stderr
    assert "REFUSING" in result.stderr


def test_commit_msg_fails_when_no_node_toolchain(hook_repo: Path) -> None:
    """No commitlint binary and no pnpm/npx to fetch one must FAIL the commit,
    not skip message validation with only a warning."""
    msg = hook_repo / "msg.txt"
    msg.write_text("feat: valid message\n")
    result = run_hook(hook_repo, "commit-msg", str(msg), path=minimal_path(hook_repo))
    assert result.returncode == 1
    assert "commitlint" in result.stderr
    assert "REFUSING" in result.stderr


def test_pre_push_fails_when_pre_commit_missing(hook_repo: Path) -> None:
    """A push with a real range and neither uvx nor pre-commit on PATH must
    FAIL, not skip the pushed-range check with a warning."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=hook_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    stdin = f"refs/heads/main {head} refs/heads/main {ZERO_SHA}\n"
    result = run_hook(
        hook_repo,
        "pre-push",
        "origin",
        "url",
        path=minimal_path(hook_repo),
        stdin=stdin,
    )
    assert result.returncode == 1
    assert "pre-commit" in result.stderr
    assert "REFUSING" in result.stderr


def test_pre_push_checks_every_range_when_body_consumes_stdin(
    hook_repo: Path,
) -> None:
    """A multi-ref push must run the pushed-range check once per ref even when
    a loop-body command reads stdin: a stdin-hungry pre-commit invocation used
    to swallow the remaining ref lines, silently skipping every range but the
    first."""
    heads = []
    for branch in ("a", "b"):
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", f"c-{branch}"],
            cwd=hook_repo,
            env=git_env(),
            check=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=hook_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        heads.append(head)
    path = minimal_path(hook_repo)
    log = hook_repo / "pre-commit-invocations.log"
    stub = Path(path) / "pre-commit"
    # The stub drains stdin exactly like a hook-running pre-commit can, then
    # records its argv — one line per invocation. Draining uses the read
    # builtin because the stub runs under the test's minimal PATH (no cat).
    stub.write_text(
        f'#!/bin/bash\nwhile IFS= read -r _; do :; done\necho "$@" >>"{log}"\n'
    )
    stub.chmod(0o755)
    stdin = "".join(
        f"refs/heads/{branch} {head} refs/heads/{branch} {ZERO_SHA}\n"
        for branch, head in zip(("a", "b"), heads)
    )
    result = run_hook(hook_repo, "pre-push", "origin", "url", path=path, stdin=stdin)
    assert result.returncode == 0, result.stderr
    invocations = log.read_text().splitlines()
    assert len(invocations) == 2, invocations
    for head, line in zip(heads, invocations):
        assert f"--to-ref {head}" in line


def test_pre_push_noop_push_passes_without_tools(hook_repo: Path) -> None:
    """An empty ref list (nothing to push) has no range to check, so missing
    tools must not block it."""
    result = run_hook(
        hook_repo, "pre-push", "origin", "url", path=minimal_path(hook_repo)
    )
    assert result.returncode == 0, result.stderr
