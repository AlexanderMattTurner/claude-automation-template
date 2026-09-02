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

SCRIPT = REPO_ROOT / ".github" / "scripts" / "label-merge-conflicts.sh"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$CALL_LOG"
case "$1 $2" in
  "label create") exit 0 ;;
  "pr list") cat "$PR_ROWS" ;;
  "pr edit") exit 0 ;;
  # An empty value is how this stub spells an API fault: a non-zero exit, not a
  # successful call that printed nothing.
  "api repos/o/r/git/ref/heads/main") [[ -n "$BASE_TIP" ]] || exit 1; echo "$BASE_TIP" ;;
  "api repos/o/r/compare/"*) [[ -n "$COMPARE_STATUS" ]] || exit 1; echo "$COMPARE_STATUS" ;;
  *) echo "fake gh: unhandled: $*" >&2; exit 1 ;;
esac
"""


def run(
    tmp_path: Path,
    *,
    compare_status: str,
    base_tip: str = "basetip",
    labeled: bool = False,
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
                    "baseRefName": "main",
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
        # The stub exits non-zero on an empty tip or status, so one attempt is
        # enough: more only sleeps before the same fallback runs.
        "RETRY_MAX": "1",
        "CALL_LOG": str(call_log),
        "PR_ROWS": str(rows),
        "BASE_TIP": base_tip,
        "COMPARE_STATUS": compare_status,
    }
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
    assert "api repos/o/r/compare/basetip...headsha" in calls


def test_a_head_identical_to_its_base_is_not_labelled(tmp_path: Path) -> None:
    _result, calls = run(tmp_path, compare_status="identical")
    assert "--add-label merge-conflict" not in calls


def test_a_contained_head_that_is_already_labelled_is_cleared(tmp_path: Path) -> None:
    # The verdict repeats on every scan, so a PR labelled before this check
    # existed would otherwise wear the label until a human removed it.
    _result, calls = run(tmp_path, compare_status="ahead", labeled=True)
    assert "--remove-label merge-conflict" in calls


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


def test_an_unreadable_base_tip_keeps_the_conflicting_verdict(tmp_path: Path) -> None:
    # No tip means no containment answer; GitHub's verdict stands rather than a
    # failed read silently clearing a real conflict.
    _result, calls = run(tmp_path, compare_status="ahead", base_tip="")
    assert "--add-label merge-conflict" in calls
    assert "compare/" not in calls
