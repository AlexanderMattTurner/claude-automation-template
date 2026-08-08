"""The GitHub credential ladder that callers pick a usable token from.

The ladder exists because a workflow-expression chain (`${{ A || B || C }}`)
answers "which secret is configured" and never "which one can still spend
quota", so a configured-but-spent PAT stranded every caller below it. These
tests drive the real shell functions; the end-to-end consequence for a caller
lives in test_approve_if_reviewer_hold_clear.py.

# covers: .github/scripts/lib/github-token-ladder.bash
"""

import json
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

LIB = REPO_ROOT / ".github" / "scripts" / "lib" / "github-token-ladder.bash"


def _bash(body: str, env: dict[str, str], path: str | None = None):
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail; source {LIB}; {body}"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": path or "/usr/bin:/bin:/usr/local/bin", **env},
    )


def ladder(**rungs: str) -> list[str]:
    res = _bash("github_token_ladder", rungs)
    assert res.returncode == 0, res.stderr
    return res.stdout.split()


def test_the_ladder_is_ordered_org_pat_then_repo_pat_then_actions():
    assert ladder(
        GH_TOKEN_ORG_PAT="org", GH_TOKEN_REPO_PAT="repo", GH_TOKEN_ACTIONS="actions"
    ) == ["org", "repo", "actions"]


def test_an_unset_middle_rung_is_stepped_over_not_truncated():
    # The rung below an unset one must still be reached: a repo that sets only
    # the org PAT and the Actions token still gets both.
    assert ladder(GH_TOKEN_ORG_PAT="org", GH_TOKEN_ACTIONS="actions") == [
        "org",
        "actions",
    ]


def test_the_same_credential_configured_twice_is_probed_once():
    # An org-owned repo commonly sets both PAT spellings to the same token;
    # probing it twice would spend a request to learn what rung one already said.
    assert ladder(
        GH_TOKEN_ORG_PAT="same", GH_TOKEN_REPO_PAT="same", GH_TOKEN_ACTIONS="actions"
    ) == ["same", "actions"]


def _with_quota(tmp_path: Path, budgets: dict[str, dict[str, int]], **rungs: str):
    """Drive github_token_with_quota against a `gh` whose /rate_limit answers
    from `budgets` (credential -> {core, graphql} requests remaining)."""
    (tmp_path / "budgets.json").write_text(json.dumps(budgets))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = f"""#!/usr/bin/env bash
filter=""
while [[ $# -gt 0 ]]; do
  case "$1" in --jq) filter="$2"; shift 2 ;; *) shift ;; esac
done
entry="$(jq -c --arg t "${{GH_TOKEN:-}}" '.[$t] // empty' "{tmp_path}/budgets.json")"
# No entry stands for a credential whose probe fails outright (revoked token).
[[ -n "$entry" ]] || exit 1
jq -r "$filter" <<<"{{\\"resources\\":$entry}}"
"""
    (bin_dir / "gh").write_text(stub)
    (bin_dir / "gh").chmod(0o755)
    return _bash(
        "github_token_with_quota", rungs, path=f"{bin_dir}:/usr/bin:/bin:/usr/local/bin"
    )


FULL = {"core": {"remaining": 5000}, "graphql": {"remaining": 5000}}


def test_the_first_rung_with_quota_is_chosen(tmp_path: Path):
    res = _with_quota(
        tmp_path,
        {"org": {"core": {"remaining": 0}, "graphql": {"remaining": 0}}, "act": FULL},
        GH_TOKEN_ORG_PAT="org",
        GH_TOKEN_ACTIONS="act",
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "act"


def test_a_credential_out_of_graphql_quota_alone_is_skipped(tmp_path: Path):
    """REST and GraphQL carry separate budgets, and these callers span both — a
    core-only probe would pick a token that dies on its first thread query."""
    res = _with_quota(
        tmp_path,
        {
            "org": {"core": {"remaining": 5000}, "graphql": {"remaining": 0}},
            "act": FULL,
        },
        GH_TOKEN_ORG_PAT="org",
        GH_TOKEN_ACTIONS="act",
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "act"


def test_a_revoked_credential_is_stepped_over(tmp_path: Path):
    res = _with_quota(
        tmp_path, {"act": FULL}, GH_TOKEN_ORG_PAT="revoked", GH_TOKEN_ACTIONS="act"
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "act"
    assert "quota could not be read" in res.stderr


def test_every_rung_spent_reports_failure(tmp_path: Path):
    empty = {"core": {"remaining": 0}, "graphql": {"remaining": 0}}
    res = _with_quota(
        tmp_path,
        {"org": empty, "act": empty},
        GH_TOKEN_ORG_PAT="org",
        GH_TOKEN_ACTIONS="act",
    )
    assert res.returncode != 0
    assert res.stdout.strip() == "", "a spent ladder must name no credential"


def test_no_configured_rung_reports_failure(tmp_path: Path):
    res = _with_quota(tmp_path, {})
    assert res.returncode != 0
    assert res.stdout.strip() == ""
