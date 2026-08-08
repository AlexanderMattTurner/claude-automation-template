"""Behavioral tests for .github/scripts/decide-memo-base.py.

The module answers one question: which commit on this branch did the gate's work
job actually PASS on, so a later run can diff from there instead of from the
branch point. Every case below pins the same direction of failure — a doubt
prints no anchor, which costs a re-run and never a skip.
"""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests._fake_gh import run_jobs, workflow_runs, write_gh_stub
from tests._helpers import REPO_ROOT, commit_all, init_test_repo, run_capture

SCRIPT = REPO_ROOT / ".github" / "scripts" / "decide-memo-base.py"
WORKFLOW = "hook-lifecycle.yaml"
WORK_JOB = "hook-lifecycle"
REPORTER = "hook-lifecycle-passed"
ANCHOR_JOBS = f"^{WORK_JOB}$"
RUNS_PATH = f"repos/o/r/actions/workflows/{WORKFLOW}/runs"


def started(age_seconds: int) -> str:
    """An ISO timestamp `age_seconds` in the past, as the runs API reports it."""
    return (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, list[str]]:
    """A branch with four commits, oldest first."""
    repo = tmp_path / "tree"
    init_test_repo(repo)
    return repo, [commit_all(repo, f"c{n}") for n in range(4)]


def run_script(
    repo: Path,
    tmp_path: Path,
    head: str,
    runs: list[tuple[str, int, list[tuple[str, str]]]],
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    """Drive the module against a stub `gh` serving `runs` (head, age, jobs)."""
    routes: list[tuple[str, object]] = [
        (
            RUNS_PATH,
            workflow_runs(
                [
                    (i, head_sha, started(age))
                    for i, (head_sha, age, _) in enumerate(runs, start=1)
                ]
            ),
        )
    ]
    for run_id, (_, _, jobs) in enumerate(runs, start=1):
        routes.insert(0, (f"repos/o/r/actions/runs/{run_id}/jobs", run_jobs(jobs)))
    bindir = tmp_path / "bin"
    write_gh_stub(bindir, routes)
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "o/r",
        "WORKFLOW_REF": f"o/r/.github/workflows/{WORKFLOW}@refs/heads/main",
        "HEAD_BRANCH": "feature",
        "HEAD_SHA": head,
        "MEMO_ANCHOR_JOBS": ANCHOR_JOBS,
        **overrides,
    }
    return run_capture(["python3", str(SCRIPT)], cwd=repo, env=env)


def test_anchors_on_the_newest_run_whose_work_job_passed(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    repo, shas = history
    runs = [(shas[1], 3600, [(WORK_JOB, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == shas[1]


def test_a_run_whose_work_job_skipped_is_not_an_anchor(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # The run concluded success through its always() reporter while the expensive
    # job skipped. Anchoring here would inherit a green and stop watching the
    # commits between the real verification and now.
    repo, shas = history
    runs = [
        (shas[2], 60, [(WORK_JOB, "skipped"), (REPORTER, "success")]),
        (shas[1], 3600, [(WORK_JOB, "success")]),
    ]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == shas[1], result.stderr
    assert "skipped its work job" in result.stderr


def test_no_run_with_an_executed_work_job_prints_nothing(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    repo, shas = history
    runs = [(shas[1], 60, [(WORK_JOB, "skipped"), (REPORTER, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == ""
    assert result.returncode == 0, result.stderr


def test_an_anchor_older_than_the_age_bound_is_refused(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # A memoized pass also caches the world the job ran against — a registry, a
    # base image, a pinned toolchain — so an old anchor claims more than it
    # verified.
    repo, shas = history
    runs = [(shas[1], 60 * 60 * 24 * 30, [(WORK_JOB, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == ""
    assert "limit 7d" in result.stderr


def test_a_commit_absent_from_this_checkout_is_refused(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    repo, shas = history
    runs = [("0" * 40, 60, [(WORK_JOB, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == ""
    assert "not in this checkout" in result.stderr


def test_a_non_ancestor_is_refused(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # A force-push rewrites the branch, so a previously-verified commit can be a
    # real object that is no longer part of this head's history. Diffing from it
    # would produce a range that is not the branch's own.
    repo, shas = history
    subprocess.run(
        ["git", "checkout", "-q", "-b", "side", shas[0]],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    sidelined = commit_all(repo, "side-only")
    runs = [(sidelined, 60, [(WORK_JOB, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == ""
    assert "is not an ancestor" in result.stderr


def test_a_passed_run_at_the_same_head_anchors_on_that_head(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # Marking a draft ready re-fires the workflow on the SAME head SHA. The tree
    # is identical and the work job already ran on it, so the anchor is the head
    # and the memo diff is empty.
    repo, shas = history
    runs = [(shas[3], 60, [(WORK_JOB, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == shas[3], result.stderr


def test_a_skipped_run_at_the_same_head_is_not_an_anchor(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # The draft run of a skip-on-draft workflow greens through its reporter
    # without running anything, so the ready re-fire must still do the work.
    repo, shas = history
    runs = [(shas[3], 60, [(WORK_JOB, "skipped"), (REPORTER, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == ""
    assert "skipped its work job" in result.stderr


def test_an_api_fault_on_the_run_listing_falls_back_to_no_anchor(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # The workflow ref names a file the stub does not serve, so gh exits non-zero.
    # A doubt costs a re-run, never a skip.
    repo, shas = history
    runs = [(shas[1], 60, [(WORK_JOB, "success")])]
    result = run_script(
        repo,
        tmp_path,
        shas[3],
        runs,
        WORKFLOW_REF="o/r/.github/workflows/other.yaml@refs/heads/main",
    )
    assert result.stdout.strip() == ""
    assert result.returncode == 0, result.stderr
    assert "gh api" in result.stderr and "failed" in result.stderr


def test_no_anchor_pattern_computes_nothing(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # The memo is opt-in per caller: with no pattern the module must not even
    # reach the API, so an unconfigured gate costs no request.
    repo, shas = history
    runs = [(shas[1], 60, [(WORK_JOB, "success")])]
    result = run_script(repo, tmp_path, shas[3], runs, MEMO_ANCHOR_JOBS="")
    assert result.stdout.strip() == ""
    assert "no anchor pattern" in result.stderr


def test_the_anchor_pattern_must_match_the_job_name(
    tmp_path: Path, history: tuple[Path, list[str]]
) -> None:
    # A caller that names a job this workflow does not run gets no anchor, rather
    # than an anchor justified by some other job's green.
    repo, shas = history
    runs = [(shas[1], 60, [("some other job", "success")])]
    result = run_script(repo, tmp_path, shas[3], runs)
    assert result.stdout.strip() == ""
    assert "skipped its work job" in result.stderr
