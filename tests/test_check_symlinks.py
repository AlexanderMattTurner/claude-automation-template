"""Tests for .github/scripts/check-symlinks.sh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def commit_all(repo: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture", "--allow-empty"],
        cwd=repo,
        check=True,
        env=env,
    )


def run_script(repo: Path, copy_script) -> subprocess.CompletedProcess:
    script = copy_script("check-symlinks.sh", repo)
    return subprocess.run(
        ["bash", str(script)], cwd=repo, capture_output=True, text=True
    )


def test_passes_when_no_symlinks(empty_git_repo: Path, copy_script) -> None:
    (empty_git_repo / "regular.txt").write_text("hi")
    commit_all(empty_git_repo)
    result = run_script(empty_git_repo, copy_script)
    assert result.returncode == 0, result.stderr


def test_passes_with_relative_symlink(empty_git_repo: Path, copy_script) -> None:
    (empty_git_repo / "target.txt").write_text("hi")
    (empty_git_repo / "link").symlink_to("target.txt")
    commit_all(empty_git_repo)
    result = run_script(empty_git_repo, copy_script)
    assert result.returncode == 0, result.stderr


def test_fails_with_absolute_symlink(empty_git_repo: Path, copy_script) -> None:
    (empty_git_repo / "link").symlink_to("/etc/passwd")
    commit_all(empty_git_repo)
    result = run_script(empty_git_repo, copy_script)
    assert result.returncode == 1
    assert "link -> /etc/passwd" in result.stdout + result.stderr


def test_ignores_untracked_absolute_symlink(empty_git_repo: Path, copy_script) -> None:
    """Untracked links aren't anyone else's problem yet."""
    commit_all(empty_git_repo)
    (empty_git_repo / "link").symlink_to("/etc/passwd")
    result = run_script(empty_git_repo, copy_script)
    assert result.returncode == 0, result.stderr
