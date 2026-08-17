"""cancel-pr-runs.sh must say when a run sweep may have missed in-flight runs
past its page limit, not silently leave them uncancelled — the same class of
gap `label-merge-conflicts.sh`'s own sweep-limit warning covers.

Drives the real script as a subprocess against a fake `gh` that returns a fixed
number of already-completed runs, so the script finds nothing to cancel and the
only observable behavior is the warning itself.
"""

import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "cancel-pr-runs.sh"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$CALL_LOG"
case "$1 $2" in
  "run list") cat "$RUNS_JSON" ;;
  "run cancel") exit 0 ;;
  *) echo "fake gh: unhandled: $*" >&2; exit 1 ;;
esac
"""


def run(
    tmp_path: Path, *, run_count: int, sweep_limit: int
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(FAKE_GH)
    stub.chmod(0o755)

    # All "completed", so the script filters every run out and never calls
    # `gh run cancel` — the warning is the only thing under test.
    runs = tmp_path / "runs.json"
    runs.write_text(
        "["
        + ",".join(
            f'{{"databaseId": {i}, "status": "completed", "headSha": "dead"}}'
            for i in range(1, run_count + 1)
        )
        + "]"
    )

    call_log = tmp_path / "gh-calls.txt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_TOKEN": "x",
        "REPO": "o/r",
        "HEAD_REF": "feature",
        "HEAD_SHA": "beefdead",
        "RUN_SWEEP_LIMIT": str(sweep_limit),
        "CALL_LOG": str(call_log),
        "RUNS_JSON": str(runs),
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_a_full_page_warns_the_sweep_may_have_missed_runs(tmp_path: Path) -> None:
    result = run(tmp_path, run_count=3, sweep_limit=3)
    assert "::warning::" in result.stdout
    assert "3-run limit" in result.stdout


def test_a_partial_page_does_not_warn(tmp_path: Path) -> None:
    result = run(tmp_path, run_count=2, sweep_limit=3)
    assert "::warning::" not in result.stdout


def test_an_empty_page_does_not_warn(tmp_path: Path) -> None:
    result = run(tmp_path, run_count=0, sweep_limit=3)
    assert "::warning::" not in result.stdout
