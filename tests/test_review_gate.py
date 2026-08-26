"""review-gate.sh decides whether the "Automated review posted" required check
goes green, so the only thing that matters about it is WHICH reviews it credits.

The `gh` stub here RUNS the script's own `--jq` filter over a canned reviews
payload instead of returning a pre-filtered answer. The whole safety property
lives inside that filter, so a stub that ignored `--jq` would report the gate
working while testing nothing.

Two invariants, both of which the pre-fix filter (`select(.state != "DISMISSED")
| .user.login`) violated — each test that pins one also runs the pre-fix filter
over the same payload and asserts it answered the other way, so the test cannot
pass vacuously against a gate that never changed.
"""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "review-gate.sh"
LIB_REL = ".github/scripts/lib/reviewer-login.bash"
REVIEWER_SCRIPTS = (
    "review-gate.sh",
    "approve-if-reviewer-hold-clear.sh",
)

BOT = "github-actions[bot]"
HEAD_SHA = "cafebabe"

# The filter this gate shipped with. Kept verbatim so every invariant below can
# show the fixture it rejects is one the old gate accepted.
PRE_FIX_JQ = '.[] | select(.state != "DISMISSED") | .user.login // ""'


def review(state: str, *, author: str = BOT, body: str = "Automated review.") -> dict:
    return {"state": state, "body": body, "user": {"login": author}}


def run_gate(tmp_path: Path, reviews: list[dict]) -> str:
    """Run the gate over `reviews`; return the status state it posted."""
    (tmp_path / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    log = tmp_path / "gh-calls.txt"
    stub = f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{log}"
if [[ "$2" == "--paginate" ]]; then
  filter=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --jq) filter="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  jq -r "$filter" "{tmp_path}/reviews.json"
  exit 0
fi
exit 0
"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(stub, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)

    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
            "GH_TOKEN": "t",
            "GH_REPO": "o/r",
            "PR": "438",
            "HEAD_SHA": HEAD_SHA,
        },
    )
    assert res.returncode == 0, res.stderr
    calls = log.read_text(encoding="utf-8")
    assert f"statuses/{HEAD_SHA}" in calls, f"the gate posted no status: {calls}"
    states = [s for s in ("state=success", "state=pending") if s in calls]
    assert len(states) == 1, f"expected exactly one verdict, got {states}: {calls}"
    return states[0].removeprefix("state=")


def pre_fix_verdict(reviews: list[dict]) -> str:
    """What the gate's original filter answered for the same payload."""
    out = subprocess.run(
        ["jq", "-r", PRE_FIX_JQ],
        input=json.dumps(reviews),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return "success" if out else "pending"


def test_the_reviewers_own_review_clears_the_gate(tmp_path: Path) -> None:
    """The clean path: no fix may green a gate that stopped working."""
    assert run_gate(tmp_path, [review("COMMENTED")]) == "success"


def test_an_approval_clears_the_gate(tmp_path: Path) -> None:
    """auto-approve-skipped-pr.sh and approve-if-reviewer-hold-clear.sh both post
    an APPROVE under the reviewer's identity; both must still clear the gate."""
    assert run_gate(tmp_path, [review("APPROVED")]) == "success"


def test_a_dismissed_review_leaves_the_gate_pending(tmp_path: Path) -> None:
    assert run_gate(tmp_path, [review("DISMISSED")]) == "pending"


@pytest.mark.parametrize(
    "author", ["pr-author", "outside-contributor", "dependabot[bot]"]
)
def test_a_non_reviewer_review_never_clears_the_gate(
    tmp_path: Path, author: str
) -> None:
    """INVARIANT 1. The gate claims an AUTOMATED review exists. Any actor's review
    counting made it self-clearing: a PR author submits a one-word COMMENT review
    on their own PR and a required merge lever goes green with no reviewer run."""
    payload = [review("COMMENTED", author=author)]
    assert run_gate(tmp_path, payload) == "pending"
    assert pre_fix_verdict(payload) == "success", (
        "fixture no longer exercises the bug: the pre-fix filter must have "
        "credited this review, or this test proves nothing"
    )


def test_a_body_less_reviewer_review_never_clears_the_gate(tmp_path: Path) -> None:
    """INVARIANT 2. GitHub synthesizes a body-less COMMENTED review around every
    standalone review comment, and this repo posts such comments under
    the reviewer's own identity on each auto-resolved thread. Crediting it greens
    the gate for a PR the reviewer is still holding."""
    payload = [review("COMMENTED", body="")]
    assert run_gate(tmp_path, payload) == "pending"
    assert pre_fix_verdict(payload) == "success", (
        "fixture no longer exercises the bug: the pre-fix filter must have "
        "credited this body-less review, or this test proves nothing"
    )


def test_a_real_review_still_clears_a_gate_full_of_noise(tmp_path: Path) -> None:
    """Both filters at once, in the order a live PR accumulates them."""
    payload = [
        review("COMMENTED", author="pr-author", body="looks fine to me"),
        review("COMMENTED", body=""),
        review("CHANGES_REQUESTED"),
    ]
    assert run_gate(tmp_path, payload) == "success"


# ── The library the four reviewer scripts share must reach every runner ──────


def _sparse_checkout_lists():
    """(workflow, step name, sparse-checkout entries) for every checkout step."""
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for job in (doc.get("jobs") or {}).values():
            for step in (job or {}).get("steps") or []:
                sparse = (step.get("with") or {}).get("sparse-checkout")
                if not sparse:
                    continue
                entries = [e.strip() for e in str(sparse).split("\n") if e.strip()]
                yield path.name, step.get("name"), entries


def test_every_sparse_checkout_of_a_reviewer_script_also_takes_the_shared_lib():
    """A sparse checkout naming individual FILES gets exactly those files. All
    four reviewer scripts now `source` lib/reviewer-login.bash, so a list that
    names the script without the library leaves the script to die at its `source`
    line under `set -e` — with the gate's own bootstrap arm unable to tell that
    apart from a bad install."""
    checked = []
    for workflow, step, entries in _sparse_checkout_lists():
        # A list naming the whole scripts directory already carries lib/.
        if any(e == ".github/scripts" for e in entries):
            continue
        named = [e for e in entries if Path(e).name in REVIEWER_SCRIPTS]
        if not named:
            continue
        checked.append((workflow, step, named))
        assert LIB_REL in entries, (
            f"{workflow} / {step!r} sparse-checks out {named} without {LIB_REL}; "
            "the script sources that library and will die under set -e"
        )
    assert checked, (
        "no sparse-checkout list names a reviewer script any more — this guard "
        "would now pass without checking anything; re-point it or delete it"
    )


def test_every_reviewer_script_uses_the_shared_login_library():
    """The identity predicate was copied into four scripts; the point of the
    library is that it stops being four. A script that re-derives the bare login
    locally has forked the predicate again."""
    for name in REVIEWER_SCRIPTS:
        text = (REPO_ROOT / ".github" / "scripts" / name).read_text(encoding="utf-8")
        assert "lib/reviewer-login.bash" in text and "reviewer_login_init" in text, (
            f"{name} does not source the shared reviewer-login library"
        )
        assert "REVIEWER_LOGIN%" not in text, (
            f"{name} re-derives REVIEWER_LOGIN_BARE itself instead of calling "
            "reviewer_login_init"
        )
        assert 'sub("\\\\[bot\\\\]$"' not in text, (
            f"{name} hand-writes the [bot]-stripping jq instead of using the "
            "library's select clause"
        )
