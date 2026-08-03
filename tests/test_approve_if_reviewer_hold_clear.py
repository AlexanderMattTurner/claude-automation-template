"""The hold-clear script decides whether an automated reviewer's hold may be
lifted without a human, so its refusals matter more than its successes.

The `gh` stub here RUNS the script's own `--jq` filters over canned GraphQL
responses rather than returning pre-filtered output. That is deliberate: the
safety property — never dismiss a review this bot did not write — lives entirely
inside one of those filters, and a stub that ignored `--jq` would report it
working while testing nothing.
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)
SCRIPT = REPO_ROOT / ".github" / "scripts" / "approve-if-reviewer-hold-clear.sh"

BOT = "github-actions"


def thread(author: str = BOT, resolved: bool = True) -> dict:
    return {
        "isResolved": resolved,
        "comments": {"nodes": [{"author": {"login": author}}]},
    }


def review(
    state: str, author: str = BOT, at: str = "2026-01-01T00:00:00Z", rid: int = 1
) -> dict:
    return {
        "databaseId": rid,
        "author": {"login": author},
        "state": state,
        "submittedAt": at,
    }


def graphql_payloads(threads: list[dict], reviews: list[dict]) -> tuple[str, str]:
    page = {"hasNextPage": False, "endCursor": None}
    t = {
        "data": {
            "repository": {
                "pullRequest": {"reviewThreads": {"pageInfo": page, "nodes": threads}}
            }
        }
    }
    r = {
        "data": {
            "repository": {
                "pullRequest": {"reviews": {"pageInfo": page, "nodes": reviews}}
            }
        }
    }
    return json.dumps(t), json.dumps(r)


def run(
    tmp_path: Path,
    *,
    threads: list[dict],
    reviews: list[dict],
    approve_error: str | None = None,
    dismiss_error: str | None = None,
):
    """Drive the script with a `gh` that executes its real --jq filters."""
    threads_json, reviews_json = graphql_payloads(threads, reviews)
    (tmp_path / "threads.json").write_text(threads_json)
    (tmp_path / "reviews.json").write_text(reviews_json)
    log = tmp_path / "gh-calls.txt"

    def arm(err: str | None) -> str:
        if err is None:
            return "exit 0"
        return 'printf "%s\\n" ' + json.dumps(err) + " >&2; exit 1"

    approve_arm = arm(approve_error)
    dismiss_arm = arm(dismiss_error)

    # The stub picks the fixture by inspecting the query text the script passed,
    # then applies the script's own --jq to it, exactly as `gh api graphql` would.
    stub = f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{log}"
if [[ "$1" == "api" && "$2" == "graphql" ]]; then
  query=""; filter=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f) [[ "$2" == query=* ]] && query="${{2#query=}}"; shift 2 ;;
      --jq) filter="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ "$query" == *reviewThreads* ]]; then src="{tmp_path}/threads.json"; else src="{tmp_path}/reviews.json"; fi
  jq -r "$filter" "$src"
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "review" ]]; then
  {approve_arm}
fi
if [[ "$1" == "api" && "$2" == "--method" && "$3" == "PUT" ]]; then
  {dismiss_arm}
fi
exit 0
"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(stub)
    (bin_dir / "gh").chmod(0o755)

    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
            "GH_TOKEN": "x",
            "GH_REPO": "o/r",
            "PR": "438",
        },
    )
    calls = log.read_text() if log.exists() else ""
    return res, calls


def dismissed(calls: str) -> bool:
    return "dismissals" in calls


CLEARED = dict(threads=[thread(resolved=True)], reviews=[review("CHANGES_REQUESTED")])
SELF_APPROVAL = "GraphQL: Can not approve your own pull request (addPullRequestReview)"
ACTIONS_BARRED = "GitHub Actions is not permitted to approve pull requests"


def test_a_self_approval_refusal_dismisses_the_stale_hold(tmp_path: Path):
    res, calls = run(tmp_path, **CLEARED, approve_error=SELF_APPROVAL)
    assert res.returncode == 0, res.stderr
    assert dismissed(calls), (
        "the hold was clear and approval impossible — it must be dismissed"
    )
    assert "dismissed the reviewer's stale CHANGES_REQUESTED" in res.stderr


def test_an_actions_token_refusal_dismisses_the_stale_hold(tmp_path: Path):
    res, calls = run(tmp_path, **CLEARED, approve_error=ACTIONS_BARRED)
    assert res.returncode == 0, res.stderr
    assert dismissed(calls)


def test_a_successful_approval_dismisses_nothing(tmp_path: Path):
    # Approval already cleared the hold; dismissing on top would be gratuitous.
    res, calls = run(tmp_path, **CLEARED)
    assert res.returncode == 0, res.stderr
    assert not dismissed(calls)


def test_a_humans_changes_requested_is_never_dismissed(tmp_path: Path):
    # THE safety case. A human hold must survive, and needs that human to lift it.
    res, calls = run(
        tmp_path,
        threads=[thread(resolved=True)],
        reviews=[
            review("COMMENTED", rid=1),
            review(
                "CHANGES_REQUESTED", author="a-human", at="2026-01-02T00:00:00Z", rid=2
            ),
        ],
        approve_error=SELF_APPROVAL,
    )
    assert res.returncode == 0, res.stderr
    assert not dismissed(calls), (
        "a review this bot did not write must never be dismissed"
    )
    assert "no active CHANGES_REQUESTED" in res.stderr


def test_a_comment_only_hold_dismisses_nothing(tmp_path: Path):
    # A COMMENTED review does not block a merge, so there is nothing to dismiss.
    res, calls = run(
        tmp_path,
        threads=[thread(resolved=True)],
        reviews=[review("COMMENTED")],
        approve_error=SELF_APPROVAL,
    )
    assert res.returncode == 0, res.stderr
    assert not dismissed(calls)
    assert "does not block a merge" in res.stderr


def test_the_newest_bot_changes_requested_is_the_one_dismissed(tmp_path: Path):
    # A CHANGES_REQUESTED keeps blocking until dismissed or superseded by an
    # APPROVED from the same reviewer — a later COMMENTED does not clear it. So
    # the blocking review is routinely NOT the reviewer's latest review.
    res, calls = run(
        tmp_path,
        threads=[thread(resolved=True)],
        reviews=[
            review("CHANGES_REQUESTED", at="2026-01-01T00:00:00Z", rid=11),
            review("CHANGES_REQUESTED", at="2026-01-03T00:00:00Z", rid=33),
            review("COMMENTED", at="2026-01-04T00:00:00Z", rid=44),
        ],
        approve_error=SELF_APPROVAL,
    )
    assert res.returncode == 0, res.stderr
    assert "/reviews/33/dismissals" in calls
    assert "/reviews/11/dismissals" not in calls


def test_a_failing_dismissal_exits_non_zero(tmp_path: Path):
    # Unlike the approval refusals, a failed dismissal is not structural — nothing
    # about this PR makes it permanently impossible, so it must be seen.
    res, _ = run(
        tmp_path,
        **CLEARED,
        approve_error=SELF_APPROVAL,
        dismiss_error="HTTP 403: Resource not accessible",
    )
    assert res.returncode != 0
    assert "failed to dismiss" in res.stderr


def test_an_unresolved_thread_blocks_both_approval_and_dismissal(tmp_path: Path):
    # The hold is live. Nothing may clear it — this is the precondition the whole
    # dismissal path rests on.
    res, calls = run(
        tmp_path,
        threads=[thread(resolved=False)],
        reviews=[review("CHANGES_REQUESTED")],
        approve_error=SELF_APPROVAL,
    )
    assert res.returncode == 0
    assert not dismissed(calls)
    assert "pr review" not in calls, "a live hold must not even attempt an approval"
    assert "still open; not approving" in res.stderr


@pytest.mark.parametrize("state", ["APPROVED", "DISMISSED"])
def test_a_reviewer_not_holding_dismisses_nothing(tmp_path: Path, state: str):
    res, calls = run(
        tmp_path,
        threads=[thread(resolved=True)],
        reviews=[review(state)],
        approve_error=SELF_APPROVAL,
    )
    assert res.returncode == 0
    assert not dismissed(calls)
    assert "no live hold to clear" in res.stderr
