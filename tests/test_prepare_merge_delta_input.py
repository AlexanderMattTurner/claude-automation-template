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

ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)
SCRIPTS = ROOT / ".github" / "scripts"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def commit(repo: Path, text: str, message: str) -> None:
    (repo / "f.txt").write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def origin_with_evil_merge(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@t")
    git(origin, "config", "user.name", "t")
    commit(origin, "one\ntwo\nthree\n", "base")
    git(origin, "checkout", "-q", "-b", "side")
    commit(origin, "one\nTHEIRS\nthree\n", "side change")
    git(origin, "checkout", "-q", "-b", "pr", "main")
    commit(origin, "one\nOURS\nthree\n", "pr change")
    subprocess.run(
        ["git", "-C", str(origin), "merge", "--no-edit", "side"],
        capture_output=True,
        check=False,
    )
    commit(origin, "one\nOURS\nTHEIRS\nINVENTED\nthree\n", "merge side")
    git(origin, "update-ref", "refs/pull/7/head", "pr")
    git(origin, "checkout", "-q", "main")
    return origin


def test_a_sanitizer_crash_reaches_the_log(tmp_path: Path):
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin_with_evil_merge(tmp_path)), str(work))
    (work / ".github" / "scripts").mkdir(parents=True)
    for name in (
        "prepare-merge-delta-input.sh",
        "remerge-diff-report.py",
        "sanitize-pr-input.mjs",
    ):
        shutil.copy(SCRIPTS / name, work / ".github" / "scripts" / name)
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
