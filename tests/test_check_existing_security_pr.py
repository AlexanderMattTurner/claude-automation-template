"""Tests for .github/scripts/check-existing-security-pr.sh's merge of the default
branch into the open rollup branch.

`gh` is faked because it would need the GitHub API; git runs for real against a
local origin, cloned at depth 1 the way actions/checkout clones by default.
"""

import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-existing-security-pr.sh"
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]
ROLLUP = "security-fixes/x"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_ID, *args], cwd=cwd, check=True, capture_output=True)


def run_script(tmp_path: Path, *, related: bool) -> subprocess.CompletedProcess:
    """Build an origin whose rollup branch forks from `main` (or shares no history
    with it), advance `main`, clone it at depth 1, and run the script there."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "commit", "-q", "--allow-empty", "-m", "base")
    git(origin, "checkout", "-q", *(["-b"] if related else ["--orphan"]), ROLLUP)
    git(origin, "commit", "-q", "--allow-empty", "-m", "rollup")
    git(origin, "checkout", "-q", "main")
    git(origin, "commit", "-q", "--allow-empty", "-m", "main moves on")
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{origin}", str(work))
    # actions/checkout keeps the default refspec, unlike a plain `--depth` clone.
    git(work, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(f"#!/usr/bin/env bash\necho {ROLLUP}\n", encoding="utf-8")
    fake_gh.chmod(0o755)
    # No global or system git config, so no identity: a GitHub runner has none.
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GH_TOKEN": "fake",
        "DEFAULT_BRANCH": "main",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_merges_main_into_a_rollup_from_a_depth_1_checkout(tmp_path: Path) -> None:
    result = run_script(tmp_path, related=True)
    assert result.returncode == 0, result.stderr
    out = (tmp_path / "out").read_text(encoding="utf-8")
    assert out == f"branch={ROLLUP}\nexists=true\n"


def test_a_refused_merge_exits_1_with_gits_own_error(tmp_path: Path) -> None:
    result = run_script(tmp_path, related=False)
    assert result.returncode == 1
    assert "refusing to merge unrelated histories" in result.stderr
    assert "There is no merge to abort" not in result.stderr
