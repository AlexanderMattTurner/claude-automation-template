"""prepare-merge-delta-input.sh must put a sanitizer crash in the job log.

The sanitizer writes its report on stderr, and the script sends that stream to a
report file, so an unguarded crash leaves a red job with no reason in the log.
The fixture is a real origin repo whose refs/pull/7/head carries an evil merge.
The sanitizer crashes for real: the copied tree has no node_modules, so its
import of agent-input-sanitizer fails.
"""

import os
import shutil
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT, commit_files, git_out, init_test_repo


def origin_with_evil_merge(origin: Path) -> Path:
    init_test_repo(origin)
    commit_files(origin, {"f.txt": "one\ntwo\nthree\n"}, "base")
    git_out(origin, "checkout", "-q", "-b", "side")
    commit_files(origin, {"f.txt": "one\nTHEIRS\nthree\n"}, "side change")
    git_out(origin, "checkout", "-q", "-b", "pr", "main")
    commit_files(origin, {"f.txt": "one\nOURS\nthree\n"}, "pr change")
    res = subprocess.run(
        ["git", "-C", str(origin), "merge", "--no-edit", "side"],
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0, "fixture must actually conflict"
    commit_files(origin, {"f.txt": "one\nOURS\nTHEIRS\nINVENTED\nthree\n"}, "merge")
    git_out(origin, "update-ref", "refs/pull/7/head", "pr")
    git_out(origin, "checkout", "-q", "main")
    return origin


def test_a_sanitizer_crash_reaches_the_log(tmp_path: Path):
    work = tmp_path / "work"
    origin = origin_with_evil_merge(tmp_path / "origin")
    git_out(tmp_path, "clone", "-q", str(origin), str(work))
    scripts = work / ".github" / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "prepare-merge-delta-input.sh",
        "remerge-diff-report.py",
        "sanitize-pr-input.mjs",
    ):
        shutil.copy(REPO_ROOT / ".github" / "scripts" / name, scripts / name)
    out = tmp_path / "github_output"

    res = subprocess.run(
        ["bash", ".github/scripts/prepare-merge-delta-input.sh"],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "PR": "7",
            "PR_INPUT_DIR": str(tmp_path / "pr-input"),
            "GITHUB_OUTPUT": str(out),
            "GH_TOKEN": "unused",
        },
    )

    assert res.returncode == 1, res.stderr
    assert "::error::the input sanitizer crashed" in res.stderr
    # The sanitizer's own error, not just the script's summary of it.
    assert "Cannot find package 'agent-input-sanitizer'" in res.stderr
    assert not out.exists(), "a crashed run must not emit has_deltas"
