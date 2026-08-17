"""label-merge-conflicts.sh must say when a full-repo sweep may have missed PRs
past its page limit, not silently under-sweep them — the same "no silent caps"
rule the script already follows for PRs still UNKNOWN after MAX_PASSES retries.

Drives the real script as a subprocess against a fake `gh` that returns a fixed
number of already-CONFLICTING, already-labeled PR rows, so the script takes no
label-editing action and the only observable behavior is the warning itself.
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
  # Real `gh pr view` emits ONE PR object, then applies its own --jq (the
  # script's last argument) to it — so a script that dropped --jq would get an
  # object where list_prs() iterates an array.
  "pr view")
    obj="$(jq -c '.[0]' "$PR_ROWS")"
    if [[ "$*" == *--jq* ]]; then jq -c "${@: -1}" <<<"$obj"; else echo "$obj"; fi
    ;;
  "pr edit") exit 0 ;;
  *) echo "fake gh: unhandled: $*" >&2; exit 1 ;;
esac
"""


def run(
    tmp_path: Path,
    *,
    row_count: int,
    sweep_limit: int,
    max_passes: int = 1,
    mergeable: str = "CONFLICTING",
    labeled: bool = True,
    pr_number: str | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(FAKE_GH)
    stub.chmod(0o755)

    # Already labeled by default, so a CONFLICTING row takes no label-editing
    # action and the warning is the only thing under test. An UNKNOWN row
    # exhausts every pass instead, to drive the multi-pass dedup case; an
    # unlabeled row drives the label-editing path itself.
    rows = tmp_path / "pr-rows.json"
    rows.write_text(
        json.dumps(
            [
                {
                    "number": n,
                    "mergeable": mergeable,
                    "labels": [{"name": "merge-conflict"}] if labeled else [],
                }
                for n in range(1, row_count + 1)
            ]
        )
    )

    call_log = tmp_path / "gh-calls.txt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_TOKEN": "x",
        "REPO": "o/r",
        "SWEEP_LIMIT": str(sweep_limit),
        "MAX_PASSES": str(max_passes),
        "RETRY_DELAY_SECS": "0",
        "CALL_LOG": str(call_log),
        "PR_ROWS": str(rows),
    }
    if pr_number is not None:
        env["PR_NUMBER"] = pr_number
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_a_full_page_warns_the_sweep_may_have_missed_prs(tmp_path: Path) -> None:
    result = run(tmp_path, row_count=3, sweep_limit=3)
    assert "::warning::" in result.stderr
    assert "3-PR limit" in result.stderr
    # Non-vacuous coverage of SWEEP_LIMIT actually reaching `gh`: a hardcoded
    # --limit would still pass every other assertion here.
    call_log = (tmp_path / "gh-calls.txt").read_text()
    assert "--limit 3" in call_log


def test_a_partial_page_does_not_warn(tmp_path: Path) -> None:
    result = run(tmp_path, row_count=2, sweep_limit=3)
    assert "::warning::" not in result.stderr


def test_an_empty_page_does_not_warn(tmp_path: Path) -> None:
    result = run(tmp_path, row_count=0, sweep_limit=3)
    assert "::warning::" not in result.stderr


def test_an_empty_page_does_not_warn_even_at_a_limit_of_one(tmp_path: Path) -> None:
    # A line-counting implementation renders zero PRs as one blank TSV line and
    # miscounts it as a full page at SWEEP_LIMIT=1; jq's own array length does not.
    result = run(tmp_path, row_count=0, sweep_limit=1)
    assert "::warning::" not in result.stderr


def test_a_capped_repeat_sweep_warns_only_once(tmp_path: Path) -> None:
    # MAX_PASSES retries the same capped page while any PR is still UNKNOWN; the
    # cap warning must not repeat once per pass.
    result = run(
        tmp_path, row_count=3, sweep_limit=3, max_passes=3, mergeable="UNKNOWN"
    )
    assert result.stderr.count("::warning::open-PR sweep") == 1
    # Non-vacuous: prove the retry loop actually ran all 3 passes — otherwise
    # this would pass just as well against a loop that never retried at all.
    call_log = (tmp_path / "gh-calls.txt").read_text()
    assert call_log.count("pr list") == 3


def test_a_pr_number_scoped_run_labels_an_unlabeled_conflicting_pr(
    tmp_path: Path,
) -> None:
    # PR_NUMBER routes fetch_page() through `gh pr view --jq '[.]'` instead of
    # `gh pr list` — a different gh invocation whose wrapped-array JSON shape
    # must still round-trip through list_prs() the same way. Unlabeled (unlike
    # every other case here) so the label-editing action itself is observed.
    result = run(tmp_path, row_count=1, sweep_limit=100, labeled=False, pr_number="1")
    assert "::warning::" not in result.stderr
    call_log = (tmp_path / "gh-calls.txt").read_text()
    assert "pr edit 1 --repo o/r --add-label merge-conflict" in call_log
