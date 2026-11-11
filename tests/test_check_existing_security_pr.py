"""Tests for .github/scripts/check-existing-security-pr.sh's merge failure path.

The script merges the default branch into the open rollup branch. `gh` is faked
(it would need the GitHub API); git runs for real against a local origin.
"""

import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-existing-security-pr.sh"
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_ID, *args], cwd=cwd, check=True, capture_output=True)


def run_against_unrelated_rollup(tmp_path: Path) -> subprocess.CompletedProcess:
    """Origin holds a `main` and a rollup branch that share no history, the
    shape a depth-1 checkout gives the two."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "commit", "-q", "--allow-empty", "-m", "main")
    git(origin, "checkout", "-q", "--orphan", "security-fixes/x")
    git(origin, "commit", "-q", "--allow-empty", "-m", "rollup")
    git(origin, "checkout", "-q", "main")
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text("#!/usr/bin/env bash\necho security-fixes/x\n", encoding="utf-8")
    fake_gh.chmod(0o755)
    env = {
        **os.environ,
        "GH_TOKEN": "fake",
        "DEFAULT_BRANCH": "main",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=work, env=env, capture_output=True, text=True
    )


def test_a_refused_merge_exits_1_with_gits_own_error(tmp_path: Path) -> None:
    result = run_against_unrelated_rollup(tmp_path)
    assert result.returncode == 1
    assert "refusing to merge unrelated histories" in result.stderr
    assert "There is no merge to abort" not in result.stderr
