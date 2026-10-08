"""cancel-pr-runs.sh must cancel only runs created before the PR closed.

The runs GitHub dispatches for the close event carry the PR's head SHA, so a
head-SHA-only sweep kills the close handlers (phone-home) it should spare. The
sweep also must not cancel the job that runs it.

Drives the real script as a subprocess against a fake `gh` and asserts which run
ids reach `gh run cancel`. The fake stands in for the GitHub API, which cannot
run in a test.
"""

import json
import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "cancel-pr-runs.sh"
CLOSED_AT = "2026-03-01T12:00:00Z"
SHA = "beefdead"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$CALL_LOG"
case "$1 $2" in
  "run list") cat "$RUNS_JSON" ;;
  "run cancel") exit 0 ;;
  *) echo "fake gh: unhandled: $*" >&2; exit 1 ;;
esac
"""


def run_in_flight(id_: int, created_at: str) -> dict[str, object]:
    return {
        "databaseId": id_,
        "status": "in_progress",
        "headSha": SHA,
        "createdAt": created_at,
    }


def sweep(
    tmp_path: Path,
    runs: list[dict[str, object]],
    *,
    drop: tuple[str, ...] = (),
    **extra_env: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(FAKE_GH, encoding="utf-8")
    stub.chmod(0o755)
    runs_file = tmp_path / "runs.json"
    runs_file.write_text(json.dumps(runs), encoding="utf-8")
    call_log = tmp_path / "gh-calls.txt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_TOKEN": "x",
        "REPO": "o/r",
        "HEAD_REF": "feature",
        "HEAD_SHA": SHA,
        "CLOSED_AT": CLOSED_AT,
        "RUNS_JSON": str(runs_file),
        "CALL_LOG": str(call_log),
        # Pinned empty so an ambient run id from CI cannot spare a fixture run.
        "GITHUB_RUN_ID": "",
        **extra_env,
    }
    for name in drop:
        env.pop(name, None)
    result = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=REPO_ROOT
    )
    calls = (
        call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    )
    cancelled = [c.split()[2] for c in calls if c.startswith("run cancel")]
    return result, cancelled


def test_a_run_created_before_the_close_is_cancelled(tmp_path: Path) -> None:
    result, cancelled = sweep(tmp_path, [run_in_flight(1, "2026-03-01T11:59:59Z")])
    assert result.returncode == 0
    assert cancelled == ["1"]


def test_runs_created_at_or_after_the_close_are_spared(tmp_path: Path) -> None:
    runs = [
        run_in_flight(1, "2026-03-01T11:00:00Z"),
        run_in_flight(2, CLOSED_AT),
        run_in_flight(3, "2026-03-01T12:00:05Z"),
    ]
    result, cancelled = sweep(tmp_path, runs)
    assert result.returncode == 0
    assert cancelled == ["1"]


def test_the_sweep_never_cancels_its_own_run(tmp_path: Path) -> None:
    runs = [
        run_in_flight(10, "2026-03-01T11:00:00Z"),
        run_in_flight(11, "2026-03-01T11:00:00Z"),
    ]
    result, cancelled = sweep(tmp_path, runs, GITHUB_RUN_ID="10")
    assert result.returncode == 0
    assert cancelled == ["11"]


def test_a_missing_closed_at_fails_and_cancels_nothing(tmp_path: Path) -> None:
    result, cancelled = sweep(
        tmp_path, [run_in_flight(1, "2026-03-01T11:00:00Z")], drop=("CLOSED_AT",)
    )
    assert result.returncode != 0
    assert "CLOSED_AT" in result.stderr
    assert cancelled == []
