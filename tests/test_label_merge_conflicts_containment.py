"""A head that already carries its base branch's tip cannot conflict with it.

GitHub serves a CONFLICTING verdict for such a pull request anyway — a stacked
chain whose parent branch was merged into the child is the case that produces
one — and it keeps serving it, so every scan re-labels the same pull request.
label-merge-conflicts.sh asks GitHub to compare the two commits and reads a
contained head as MERGEABLE instead.

Drives the real script as a subprocess against a fake `gh` on PATH, so the
seam under test is the CLI the script actually calls.
"""

import json
import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT
from tests._label_merge_conflicts_gh import FAKE_GH

SCRIPT = REPO_ROOT / ".github" / "scripts" / "label-merge-conflicts.sh"


def run(
    tmp_path: Path,
    *,
    compare_status: str,
    base_ref: str = "main",
    labeled: bool = False,
    pr_number: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(FAKE_GH, encoding="utf-8")
    stub.chmod(0o755)

    rows = tmp_path / "pr-rows.json"
    rows.write_text(
        json.dumps(
            [
                {
                    "number": 7,
                    "mergeable": "CONFLICTING",
                    "labels": [{"name": "merge-conflict"}] if labeled else [],
                    "headRefOid": "headsha",
                    "baseRefName": base_ref,
                }
            ]
        ),
        encoding="utf-8",
    )

    call_log = tmp_path / "gh-calls.txt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_TOKEN": "x",
        "REPO": "o/r",
        "MAX_PASSES": "1",
        "RETRY_DELAY_SECS": "0",
        # The stub exits non-zero on an empty status, so one attempt is enough:
        # more only sleeps before the same fallback runs.
        "RETRY_MAX": "1",
        "CALL_LOG": str(call_log),
        "PR_ROWS": str(rows),
        "COMPARE_STATUS": compare_status,
    }
    if pr_number is not None:
        env["PR_NUMBER"] = pr_number
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=REPO_ROOT,
    )
    return result, call_log.read_text(encoding="utf-8")


def test_a_head_ahead_of_its_base_is_not_labelled(tmp_path: Path) -> None:
    result, calls = run(tmp_path, compare_status="ahead")
    assert "--add-label merge-conflict" not in calls
    assert "::notice::" in result.stdout
    # The stub answers any compare URL, so only the logged one pins the
    # direction: reversed, `ahead` would clear the label for a head that is
    # merely behind its base.
    assert "api repos/o/r/compare/main...headsha" in calls


def test_a_head_identical_to_its_base_is_not_labelled(tmp_path: Path) -> None:
    _result, calls = run(tmp_path, compare_status="identical")
    assert "--add-label merge-conflict" not in calls


def test_a_contained_head_that_is_already_labelled_is_cleared(tmp_path: Path) -> None:
    # The verdict repeats on every scan, so a PR labelled before this check
    # existed would otherwise wear the label until a human removed it.
    _result, calls = run(tmp_path, compare_status="ahead", labeled=True)
    assert "--remove-label merge-conflict" in calls


def test_a_pr_event_reaches_the_same_verdict(tmp_path: Path) -> None:
    # PR_NUMBER routes the listing through `gh pr view`, a different call with a
    # different JSON shape, and that is the event the stacked-chain case fires
    # on. The two new fields must survive it.
    _result, calls = run(tmp_path, compare_status="ahead", pr_number="7")
    assert "pr view 7" in calls
    assert "api repos/o/r/compare/main...headsha" in calls
    assert "--add-label merge-conflict" not in calls


def test_a_head_behind_its_base_keeps_the_conflicting_verdict(tmp_path: Path) -> None:
    # The compare direction is the whole claim: `base...head` answering `ahead`
    # means the head contains the base. Written the other way round, `ahead`
    # would clear the label for a head that is merely behind.
    _result, calls = run(tmp_path, compare_status="behind")
    assert "--add-label merge-conflict" in calls


def test_a_diverged_head_keeps_the_conflicting_verdict(tmp_path: Path) -> None:
    _result, calls = run(tmp_path, compare_status="diverged")
    assert "--add-label merge-conflict" in calls


def test_an_unreadable_compare_keeps_the_conflicting_verdict(tmp_path: Path) -> None:
    # The compare answers no verdict, so GitHub's stands. The script must not
    # die here either: `set -e` mid-sweep would leave the labels half-synced.
    _result, calls = run(tmp_path, compare_status="")
    assert "--add-label merge-conflict" in calls


def test_a_base_name_carrying_a_url_delimiter_is_encoded(tmp_path: Path) -> None:
    # `gh api` reads its endpoint as a URL, so an unencoded `#` would truncate
    # the path and compare against the branch `release` instead.
    _result, calls = run(tmp_path, compare_status="ahead", base_ref="release#2")
    assert "api repos/o/r/compare/release%232...headsha" in calls


def test_a_base_name_keeps_its_slashes(tmp_path: Path) -> None:
    # GitHub takes `feature/x` as a path segment pair; `%2F` names no branch.
    _result, calls = run(tmp_path, compare_status="ahead", base_ref="feature/x")
    assert "api repos/o/r/compare/feature/x...headsha" in calls
