"""Smoke tests: every new GitHub-glue script must exit non-zero (with a clear
message) when a required env var is unset. This catches the regression where
a workflow change silently drops an env var, leaving the script to misbehave
on an empty value."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / ".github" / "scripts"

# (script, required env vars, vars to scrub from inherited environment)
CASES = [
    # check-token-scope.sh requires TOKEN
    ("check-token-scope.sh", ["TOKEN"]),
    # check-existing-security-pr.sh requires GH_TOKEN and DEFAULT_BRANCH
    ("check-existing-security-pr.sh", ["GH_TOKEN", "DEFAULT_BRANCH"]),
    # list-dependabot-prs.sh requires GH_TOKEN
    ("list-dependabot-prs.sh", ["GH_TOKEN"]),
    # fetch-security-report.sh requires GH_TOKEN and REPO
    ("fetch-security-report.sh", ["GH_TOKEN", "REPO"]),
    # request-claude-resolve.sh requires PR_NUM
    ("request-claude-resolve.sh", ["PR_NUM"]),
    # template-sync.sh requires GITHUB_OUTPUT
    ("template-sync.sh", ["GITHUB_OUTPUT"]),
    # cancel-pr-runs.sh requires REPO, HEAD_REF, HEAD_SHA, GH_TOKEN
    ("cancel-pr-runs.sh", ["REPO", "HEAD_REF", "HEAD_SHA", "GH_TOKEN"]),
    # label-merge-conflicts.sh requires GH_TOKEN and REPO
    ("label-merge-conflicts.sh", ["GH_TOKEN", "REPO"]),
    # PR-review suite (claude-pr-review.yaml and friends)
    ("prepare-pr-review-input.sh", ["PR", "PR_INPUT_DIR"]),
    ("post-pr-review.sh", ["PR", "GH_REPO", "PR_INPUT_DIR"]),
    ("auto-approve-skipped-pr.sh", ["PR", "GH_REPO"]),
    ("fetch-unresolved-review-threads.sh", ["GH_REPO", "PR", "PR_INPUT_DIR"]),
    ("resolve-addressed-threads.sh", ["PR_INPUT_DIR", "GH_RESOLVE_TOKEN"]),
    ("approve-if-reviewer-hold-clear.sh", ["GH_REPO", "PR"]),
    ("sweep-reviewer-holds.sh", ["GH_REPO"]),
    ("append-haiku-cost.sh", ["GH_REPO", "PR"]),
    # merge-delta reviewer + remerge-diff report suite
    ("prepare-merge-delta-input.sh", ["PR", "PR_INPUT_DIR"]),
    ("post-merge-delta-review.sh", ["PR", "GH_REPO", "PR_INPUT_DIR"]),
    ("precommit-range-base.sh", ["GITHUB_REPOSITORY", "GITHUB_BASE_REF", "GH_TOKEN"]),
]


@pytest.mark.parametrize("script, required_vars", CASES, ids=[c[0] for c in CASES])
def test_script_exits_when_required_var_missing(
    tmp_path: Path, script: str, required_vars: list[str]
) -> None:
    # Run with all required vars scrubbed so the script's `${VAR:?…}` guard fires.
    env = {"PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        ["bash", str(SCRIPTS / script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"{script} should exit non-zero with no env vars set, got 0"
    )
    # The unset variable should appear in stderr (bash's `${VAR:?msg}` syntax
    # prints "VAR: msg" — at minimum one of the required names must be cited).
    err = result.stderr
    assert any(var in err for var in required_vars), (
        f"{script} stderr should mention one of {required_vars}: {err}"
    )
