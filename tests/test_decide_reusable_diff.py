"""Behavioral tests for .github/scripts/decide-reusable-diff.sh.

The gate decides whether a workflow's expensive job runs, and its always()
reporter greens a skip — so every wrong `run=false` here is a required check that
reports green having verified nothing. The cases below pin each way that can
happen: a lost match, a scan that reads the wrong commits, a misconfiguration
that can only ever skip, and a range the checkout cannot diff.

The first three pin that a match survives a large `git` output. Matching with
`git … | grep -q` under `set -o pipefail` does not: grep -q exits on its first
hit and closes the pipe, the still-writing git is killed by SIGPIPE (exit 141),
pipefail turns that into a non-zero pipeline, and the `&&` guard reads the MATCH
as no-match. The fake `git` `exec cat`s a file that floods past the 64 KiB pipe
buffer AFTER the match on line 1, so a piped implementation fails deterministically
rather than only on a fast box.
"""

import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from tests._fake_gh import run_jobs, workflow_runs, write_gh_stub
from tests._helpers import REPO_ROOT, git_out, init_test_repo, run_capture

SCRIPT = REPO_ROOT / ".github" / "scripts" / "decide-reusable-diff.sh"
FLOOD_LINES = 50_000


def _fakegit_out(tmp_path: Path) -> None:
    """A `git` stub that `exec cat`s the flood file for `log`/`diff` (so a
    SIGPIPE-killed cat makes the git process exit non-zero, exactly as real git
    does), and emits nothing for an unset stream.

    `ls-files` reaches the REAL git by absolute path. The stub sits first on PATH,
    so it answers for every process the script starts, and `shell-run-closure.py`
    asks git which files the repo tracks — a question this fixture has no business
    answering. Everything else keeps the catch-all `exit 0`, which is what lets
    the fake `base`/`head` SHAs pass the script's own range checks.
    """
    real_git = shutil.which("git")
    assert real_git, "the tests drive real git for everything the stub does not fake"
    git = tmp_path / "git"
    git.write_text(
        "#!/usr/bin/env bash\n"
        f'case " $* " in *" ls-files "*) exec {real_git} "$@" ;; esac\n'
        'case "$1" in\n'
        '  log)  [[ -n "${FAKE_LOG_FILE:-}" ]] && exec cat "$FAKE_LOG_FILE" ;;\n'
        '  diff) [[ -n "${FAKE_DIFF_FILE:-}" ]] && exec cat "$FAKE_DIFF_FILE" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git.chmod(0o755)


def _flood(path: Path, first_line: str) -> Path:
    with path.open("w") as fh:
        fh.write(first_line + "\n")
        for i in range(FLOOD_LINES):
            fh.write(f"unrelated filler line {i}\n")
    return path


def _run_raw(tmp_path: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    """Run the script with the fake git on PATH; return the raw process."""
    _fakegit_out(tmp_path)
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "BASE_SHA": "base",
        "HEAD_SHA": "head",
        "PATHS_REGEX": "",
        "TRIGGER_KEYWORD": "",
        "GITHUB_OUTPUT": str(out),
    }
    env.update(env_overrides)
    return run_capture(["bash", str(SCRIPT)], env=env)


def _run(tmp_path: Path, **env_overrides: str) -> str:
    """Run the script with the fake git on PATH; return the GITHUB_OUTPUT body."""
    res = _run_raw(tmp_path, **env_overrides)
    assert res.returncode == 0, res.stderr
    return (tmp_path / "gh_output").read_text(encoding="utf-8")


def test_keyword_trigger_fires_when_match_precedes_a_large_log(tmp_path: Path) -> None:
    log = _flood(tmp_path / "log.txt", "fix(hooks): rerun [full-lifecycle]")
    output = _run(tmp_path, TRIGGER_KEYWORD="[full-lifecycle]", FAKE_LOG_FILE=str(log))
    assert "run=true" in output, output


def test_paths_trigger_fires_when_match_precedes_a_large_diff(tmp_path: Path) -> None:
    diff = _flood(tmp_path / "diff.txt", ".claude/hooks/session-setup.sh")
    output = _run(tmp_path, PATHS_REGEX=r"^\.claude/hooks/", FAKE_DIFF_FILE=str(diff))
    assert "run=true" in output, output


def test_skip_on_draft_skips_even_a_path_matching_draft(tmp_path: Path) -> None:
    # The draft skip must precede the path scan: a matching diff on a draft PR
    # still yields run=false, or the opt-in defers nothing.
    diff = _flood(tmp_path / "diff.txt", ".claude/hooks/session-setup.sh")
    output = _run(
        tmp_path,
        PATHS_REGEX=r"^\.claude/hooks/",
        SKIP_ON_DRAFT="true",
        IS_DRAFT="true",
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=false" in output, output


def test_skip_on_draft_runs_a_ready_pr(tmp_path: Path) -> None:
    diff = _flood(tmp_path / "diff.txt", ".claude/hooks/session-setup.sh")
    output = _run(
        tmp_path,
        PATHS_REGEX=r"^\.claude/hooks/",
        SKIP_ON_DRAFT="true",
        IS_DRAFT="false",
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=true" in output, output


def test_draft_without_opt_in_runs(tmp_path: Path) -> None:
    diff = _flood(tmp_path / "diff.txt", ".claude/hooks/session-setup.sh")
    output = _run(
        tmp_path,
        PATHS_REGEX=r"^\.claude/hooks/",
        IS_DRAFT="true",
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=true" in output, output


def test_no_match_does_not_trigger(tmp_path: Path) -> None:
    log = _flood(tmp_path / "log.txt", "chore: unrelated change")
    diff = _flood(tmp_path / "diff.txt", "docs/readme.md")
    output = _run(
        tmp_path,
        PATHS_REGEX=r"^\.claude/hooks/",
        TRIGGER_KEYWORD="[full-lifecycle]",
        FAKE_LOG_FILE=str(log),
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=false" in output, output


def test_paths_regex_file_resolves_the_ssot_regex(tmp_path: Path) -> None:
    """paths-regex-file points at a shell snippet defining GATE_PATHS_REGEX (the
    same file a local git hook sources); the decide must gate on that regex."""
    gate = tmp_path / "gate.sh"
    gate.write_text("GATE_PATHS_REGEX='^src/.*\\.js$'\n", encoding="utf-8")
    hit = _flood(tmp_path / "diff.txt", "src/app.js")
    output = _run(tmp_path, PATHS_REGEX_FILE=str(gate), FAKE_DIFF_FILE=str(hit))
    assert "run=true" in output, output
    miss = _flood(tmp_path / "diff2.txt", "docs/readme.md")
    output = _run(tmp_path, PATHS_REGEX_FILE=str(gate), FAKE_DIFF_FILE=str(miss))
    assert "run=false" in output, output


def test_paths_regex_file_missing_fails_red(tmp_path: Path) -> None:
    """A missing SSOT file must fail the decide job. A guessed-empty regex would
    silently skip every gated job — the fail-open this input exists to remove."""
    r = _run_raw(tmp_path, PATHS_REGEX_FILE=str(tmp_path / "nope.sh"))
    assert r.returncode != 0, r.stdout
    assert "not found" in r.stderr


def test_paths_regex_file_without_variable_fails_red(tmp_path: Path) -> None:
    gate = tmp_path / "gate.sh"
    gate.write_text("# defines nothing\n", encoding="utf-8")
    r = _run_raw(tmp_path, PATHS_REGEX_FILE=str(gate))
    assert r.returncode != 0, r.stdout
    assert "GATE_PATHS_REGEX" in r.stderr


def test_paths_regex_and_file_both_set_fails_red(tmp_path: Path) -> None:
    """Both inputs set is a misconfiguration — an inline regex beside the SSOT
    file is exactly the second copy the file input exists to eliminate."""
    gate = tmp_path / "gate.sh"
    gate.write_text("GATE_PATHS_REGEX='^src/'\n", encoding="utf-8")
    r = _run_raw(tmp_path, PATHS_REGEX="^src/", PATHS_REGEX_FILE=str(gate))
    assert r.returncode != 0, r.stdout
    assert "not both" in r.stderr


def test_no_pr_context_runs_everything(tmp_path: Path) -> None:
    """Empty base/head (workflow_dispatch, schedule) has no range to diff, so the
    gate must run rather than skip."""
    output = _run(tmp_path, BASE_SHA="", HEAD_SHA="")
    assert "run=true" in output, output


def test_zero_base_sha_fails_open(tmp_path: Path) -> None:
    """A push's `before` is all zeros on branch creation — no diffable range, so
    the gate must run rather than skip or crash."""
    output = _run(tmp_path, BASE_SHA="0" * 40)
    assert "run=true" in output, output


def test_no_trigger_configured_on_real_diff_fails_loud(tmp_path: Path) -> None:
    """A gate reaching a real PR diff with NO trigger — no paths-regex and no
    keyword — can only ever emit run=false, so its always() reporter would green
    the skip forever. A mistyped env key (PATH_REGEX for PATHS_REGEX) lands here.
    The script must fail LOUD (a red decide step), never a silent run=false."""
    res = _run_raw(tmp_path)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "no PATHS_REGEX" in res.stderr, res.stderr
    assert (tmp_path / "gh_output").read_text(encoding="utf-8") == "", (
        "must not emit a run verdict on misconfig"
    )


# --- real git ---------------------------------------------------------------


def _run_realgit(
    repo: Path, **env_overrides: str
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the script against a REAL git repo (no fake git).

    Returns the completed process and the GITHUB_OUTPUT body, because the memo
    shadow reports on stdout while the gate's verdict goes to GITHUB_OUTPUT.
    """
    out = repo / "gh_output"
    out.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "BASE_SHA": "",
        "HEAD_SHA": "",
        "PATHS_REGEX": "",
        "TRIGGER_KEYWORD": "",
        "GITHUB_OUTPUT": str(out),
    }
    env.update(env_overrides)
    res = run_capture(["bash", str(SCRIPT)], cwd=repo, env=env)
    assert res.returncode == 0, res.stderr
    return res, out.read_text(encoding="utf-8")


def _run_realgit_out(repo: Path, **env_overrides: str) -> str:
    """The GITHUB_OUTPUT body of a real-git run."""
    return _run_realgit(repo, **env_overrides)[1]


def _commit(repo: Path, rel: str, body: str, message: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    git_out(repo, "add", "-A")
    git_out(repo, "commit", "-qm", message)
    return git_out(repo, "rev-parse", "HEAD")


def _pr_with_two_commits(
    repo: Path, *, head_msg: str, earlier_msg: str
) -> tuple[str, str]:
    """A PR branch with two commits (earlier then head) on a base; (base, head)."""
    init_test_repo(repo)
    base = _commit(repo, "seed", "seed", "base: fork point")
    git_out(repo, "switch", "-qc", "pr")
    _commit(repo, "f1", "a", earlier_msg)
    return base, _commit(repo, "f2", "b", head_msg)


def test_head_scope_fires_when_head_commit_carries_keyword(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    base, head = _pr_with_two_commits(
        repo, head_msg="fix(hooks): rerun [full-lifecycle]", earlier_msg="feat: setup"
    )
    output = _run_realgit_out(
        repo,
        BASE_SHA=base,
        HEAD_SHA=head,
        TRIGGER_KEYWORD="[full-lifecycle]",
        KEYWORD_SCOPE="head",
    )
    assert "run=true" in output, output


def test_head_scope_ignores_keyword_in_an_earlier_pr_commit(tmp_path: Path) -> None:
    """keyword-scope=head: a keyword on an EARLIER PR commit (with an untagged
    head) must NOT fire — that per-commit opt-in is what stops every later push
    from re-running the costly job. The same inputs under the default 'range'
    scope DO fire, proving the scope flips the verdict and not a typo."""
    repo = tmp_path / "r"
    base, head = _pr_with_two_commits(
        repo,
        head_msg="chore: unrelated follow-up push",
        earlier_msg="fix(hooks): rerun [full-lifecycle]",
    )
    head_out = _run_realgit_out(
        repo,
        BASE_SHA=base,
        HEAD_SHA=head,
        TRIGGER_KEYWORD="[full-lifecycle]",
        KEYWORD_SCOPE="head",
    )
    assert "run=false" in head_out, head_out
    range_out = _run_realgit_out(
        repo,
        BASE_SHA=base,
        HEAD_SHA=head,
        TRIGGER_KEYWORD="[full-lifecycle]",
        KEYWORD_SCOPE="range",
    )
    assert "run=true" in range_out, range_out


def test_keyword_in_a_base_side_commit_does_not_fire(tmp_path: Path) -> None:
    """A trigger keyword on a commit that is on the BASE branch but NOT in the PR
    must not fire the gate. main advances past the fork point with a tagged commit
    and a PR branched earlier inherits base.sha = that newer tip. A 3-dot
    `git log A...B` (symmetric difference) scans that base-only title and fires a
    costly job the PR never asked for; the 2-dot `A..B` range must not."""
    repo = tmp_path / "r"
    init_test_repo(repo)
    fork = _commit(repo, "seed", "seed", "base: fork point")

    git_out(repo, "switch", "-qc", "pr")
    head = _commit(repo, "pr_file", "x", "feat: innocuous PR change")

    git_out(repo, "switch", "-q", "main")
    base = _commit(
        repo, "main_file", "y", "chore(metrics): nightly refresh [full-lifecycle]"
    )
    # The branches genuinely diverged, so 2-dot and 3-dot ranges actually differ —
    # otherwise the case could not catch the bug.
    assert fork != base != head

    output = _run_realgit_out(
        repo, BASE_SHA=base, HEAD_SHA=head, TRIGGER_KEYWORD="[full-lifecycle]"
    )
    assert "run=false" in output, output


def _pr_editing(
    repo: Path, rel: str, base_body: str, head_body: str
) -> tuple[str, str]:
    """A PR that edits `rel` from base_body to head_body; returns (base, head)."""
    init_test_repo(repo)
    base = _commit(repo, rel, base_body, "base")
    git_out(repo, "switch", "-qc", "pr")
    return base, _commit(repo, rel, head_body, "edit")


def test_comment_only_match_skips_only_when_opted_in(tmp_path: Path) -> None:
    """ignore-comment-only-changes=true: a paths-regex match whose diff is pure
    comment churn does NOT fire. The SAME range with the flag off (the default)
    DOES fire, proving the flag flips the verdict and not the diff."""
    repo = tmp_path / "r"
    base, head = _pr_editing(
        repo,
        ".claude/hooks/pin.sh",
        "# see .github/dependabot.yml\nuv==0.11.26\n",
        "# see .github/renovate.json5\nuv==0.11.26\n",
    )
    opted_in = _run_realgit_out(
        repo,
        BASE_SHA=base,
        HEAD_SHA=head,
        PATHS_REGEX=r"^\.claude/hooks/",
        IGNORE_COMMENT_ONLY="true",
    )
    assert "run=false" in opted_in, opted_in
    default = _run_realgit_out(
        repo, BASE_SHA=base, HEAD_SHA=head, PATHS_REGEX=r"^\.claude/hooks/"
    )
    assert "run=true" in default, default


def test_substantive_match_runs_even_when_opted_in(tmp_path: Path) -> None:
    """The opt-in skips ONLY comment churn: a real code change under the same
    watched path must still fire with the flag on."""
    repo = tmp_path / "r"
    base, head = _pr_editing(
        repo, ".claude/hooks/pin.sh", "# pin\nuv==0.11.26\n", "# pin\nuv==0.12.0\n"
    )
    output = _run_realgit_out(
        repo,
        BASE_SHA=base,
        HEAD_SHA=head,
        PATHS_REGEX=r"^\.claude/hooks/",
        IGNORE_COMMENT_ONLY="true",
    )
    assert "run=true" in output, output


def test_unresolvable_base_sha_fails_open(tmp_path: Path) -> None:
    """A `before` rewritten out of history resolves to no commit — the gate must
    fail open (run) rather than exit non-zero and red the decide job."""
    repo = tmp_path / "r"
    init_test_repo(repo)
    head = _commit(repo, "f", "x", "seed")
    output = _run_realgit_out(
        repo, BASE_SHA="deadbeef" * 5, HEAD_SHA=head, PATHS_REGEX=r"^\.claude/"
    )
    assert "run=true" in output, output


def test_push_range_gates_on_changed_paths(tmp_path: Path) -> None:
    """A push event's before…sha range path-gates exactly like a PR's: a merge
    touching a matching path runs the gate, and one touching nothing relevant
    skips it — the post-merge waste the push-range fallback exists to cut."""
    repo = tmp_path / "r"
    init_test_repo(repo)
    before = _commit(repo, "seed", "s", "base")
    sha = _commit(repo, ".claude/hooks/tool.sh", "t", "feat: add tool")

    matched = _run_realgit_out(
        repo, BASE_SHA=before, HEAD_SHA=sha, PATHS_REGEX=r"^\.claude/hooks/"
    )
    assert "run=true" in matched, matched
    unmatched = _run_realgit_out(
        repo, BASE_SHA=before, HEAD_SHA=sha, PATHS_REGEX="^docs/"
    )
    assert "run=false" in unmatched, unmatched


def _merge_conflict_pr(repo: Path) -> tuple[str, str, str]:
    """A PR that resolved a conflict by merging the live base into its branch.

    Layout: fork point F on main; main advances F→M_OLD→M (M touches
    .claude/hooks/); the PR branch forks at F, changes only a doc, then merges the
    CURRENT main tip (M) back in. `origin` points at the repo itself with `main` at
    M, so the script's re-anchor fetch resolves the live tip. Returns
    (stale_base=M_OLD, live_main=M, head=the merge commit).
    """
    init_test_repo(repo)
    fork = _commit(repo, "seed", "s", "base: fork point")
    stale_base = _commit(repo, "m1", "1", "chore: main advances")
    live_main = _commit(repo, ".claude/hooks/tool.sh", "t", "feat: main touches hooks")

    git_out(repo, "switch", "-qc", "pr", fork)
    _commit(repo, "docs/note.md", "- fix a thing\n", "docs: add a note")
    git_out(repo, "merge", "--no-edit", "-q", "main")
    head = git_out(repo, "rev-parse", "HEAD")

    git_out(repo, "remote", "add", "origin", str(repo))
    git_out(repo, "fetch", "-q", "origin")
    return stale_base, live_main, head


def test_merge_commit_reanchors_to_live_base_and_skips(tmp_path: Path) -> None:
    """The merge-conflict over-trigger regression. The PR's only real change is a
    doc, but its head is a merge commit that pulled in main's newer commits
    (touching .claude/hooks/). A stale webhook base.sha resolves the 3-dot
    merge-base below those commits and misattributes them to the PR, firing every
    path gate. Re-anchoring to the live base tip drops them from the range."""
    repo = tmp_path / "r"
    stale_base, _live, head = _merge_conflict_pr(repo)
    output = _run_realgit_out(
        repo,
        BASE_SHA=stale_base,
        HEAD_SHA=head,
        BASE_REF="main",
        GH_TOKEN="x",
        PATHS_REGEX=r"^\.claude/hooks/",
    )
    assert "run=false" in output, output


def test_merge_commit_over_triggers_without_reanchor(tmp_path: Path) -> None:
    """Non-vacuity: the SAME merge-commit PR with the re-anchor disabled (no
    BASE_REF) DOES fire on the stale base.sha, so the live-tip fetch is what flips
    the verdict rather than the diff itself."""
    repo = tmp_path / "r"
    stale_base, _live, head = _merge_conflict_pr(repo)
    output = _run_realgit_out(
        repo, BASE_SHA=stale_base, HEAD_SHA=head, PATHS_REGEX=r"^\.claude/hooks/"
    )
    assert "run=true" in output, output


def test_merge_commit_reanchor_keeps_the_prs_own_changes(tmp_path: Path) -> None:
    """Safety floor: re-anchoring must never exclude the PR's OWN files. A gate
    watching the doc path still fires on the same merge-commit PR."""
    repo = tmp_path / "r"
    stale_base, _live, head = _merge_conflict_pr(repo)
    output = _run_realgit_out(
        repo,
        BASE_SHA=stale_base,
        HEAD_SHA=head,
        BASE_REF="main",
        GH_TOKEN="x",
        PATHS_REGEX="^docs/",
    )
    assert "run=true" in output, output


# --- derived watched paths (pytest-targets) ---------------------------------

PYTEST_TARGET = "tests/test_decide_memo_base.py"
# In that target's collection-time import closure, and named by no gate regex.
CLOSURE_MEMBER = "tests/_fake_gh.py"


def test_pytest_targets_fire_on_a_helper_the_regex_does_not_name(
    tmp_path: Path,
) -> None:
    """The whole point of the input: an edit to a file the target IMPORTS triggers
    the gate even though no alternative of the regex names it. Non-vacuity — the
    same diff with pytest-targets unset must NOT fire, which is the silent green
    this input removes."""
    diff = _flood(tmp_path / "diff.txt", CLOSURE_MEMBER)
    skipped = _run(tmp_path, PATHS_REGEX=r"^\.claude/", FAKE_DIFF_FILE=str(diff))
    assert "run=false" in skipped, skipped
    fired = _run(
        tmp_path,
        PATHS_REGEX=r"^\.claude/",
        PYTEST_TARGETS=PYTEST_TARGET,
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=true" in fired, fired


def test_pytest_targets_alone_satisfy_the_trigger_check(tmp_path: Path) -> None:
    """A caller may derive ALL its paths, so pytest-targets counts as a configured
    trigger — without this the no-trigger guard would red every such gate."""
    diff = _flood(tmp_path / "diff.txt", CLOSURE_MEMBER)
    output = _run(tmp_path, PYTEST_TARGETS=PYTEST_TARGET, FAKE_DIFF_FILE=str(diff))
    assert "run=true" in output, output


def test_pytest_targets_do_not_fire_on_an_unrelated_file(tmp_path: Path) -> None:
    diff = _flood(tmp_path / "diff.txt", "docs/readme.md")
    output = _run(tmp_path, PYTEST_TARGETS=PYTEST_TARGET, FAKE_DIFF_FILE=str(diff))
    assert "run=false" in output, output


def test_pytest_targets_match_is_exact_not_a_prefix(tmp_path: Path) -> None:
    """Membership is whole-line, so a longer path sharing a closure member's
    prefix must not trigger — a substring match would over-run every gate."""
    diff = _flood(tmp_path / "diff.txt", f"{CLOSURE_MEMBER}.orig")
    output = _run(tmp_path, PYTEST_TARGETS=PYTEST_TARGET, FAKE_DIFF_FILE=str(diff))
    assert "run=false" in output, output


def test_bad_pytest_target_fails_red(tmp_path: Path) -> None:
    """A closure that cannot be derived must red the decide job. Falling back to
    the regex alone would silently drop exactly the paths this input adds."""
    diff = _flood(tmp_path / "diff.txt", CLOSURE_MEMBER)
    r = _run_raw(
        tmp_path,
        PATHS_REGEX=r"^\.claude/",
        PYTEST_TARGETS="tests/does_not_exist.py",
        FAKE_DIFF_FILE=str(diff),
    )
    assert r.returncode != 0, r.stdout
    assert "import closure" in r.stderr


SHELL_TARGET = ".github/scripts/run-hook-lifecycle.sh"
# The lifecycle runs this through `.hooks/pre-push`, which names it only as
# `"$git_root/.github/scripts/check-symlinks.sh"` — so the closure reaches it
# through the token's path SUFFIX. hook-lifecycle.yaml's own paths-regex no
# longer names it either, making it genuinely closure-only coverage.
SHELL_CLOSURE_MEMBER = ".github/scripts/check-symlinks.sh"


def test_shell_targets_fire_on_a_script_the_regex_does_not_name(
    tmp_path: Path,
) -> None:
    """A file the entry point reaches triggers the gate even though no alternative
    of the regex names it. Non-vacuity — the same diff with shell-targets unset
    must NOT fire, which is the silent green this input removes. The member is
    also reached only through a token suffix, so a whole-token match fails here."""
    diff = _flood(tmp_path / "diff.txt", SHELL_CLOSURE_MEMBER)
    skipped = _run(tmp_path, PATHS_REGEX="^docs/", FAKE_DIFF_FILE=str(diff))
    assert "run=false" in skipped, skipped
    fired = _run(
        tmp_path,
        PATHS_REGEX="^docs/",
        SHELL_TARGETS=SHELL_TARGET,
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=true" in fired, fired


def test_the_hook_lifecycle_gate_watches_its_closure_member_only_by_derivation() -> (
    None
):
    """The comment above SHELL_CLOSURE_MEMBER claims hook-lifecycle's own regex
    does not name that file, which is what makes the case above non-vacuous for
    the REAL gate rather than only for the test's `^docs/`. Assert it, because a
    later widening of that regex would quietly turn the claim false."""
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "hook-lifecycle.yaml").read_text()
    )
    gate = workflow["jobs"]["decide"]["with"]
    assert SHELL_TARGET in gate["shell-targets"].split()
    assert not re.search(gate["paths-regex"], SHELL_CLOSURE_MEMBER)


def test_shell_targets_do_not_fire_on_an_unrelated_file(tmp_path: Path) -> None:
    diff = _flood(tmp_path / "diff.txt", "docs/readme.md")
    output = _run(tmp_path, SHELL_TARGETS=SHELL_TARGET, FAKE_DIFF_FILE=str(diff))
    assert "run=false" in output, output


def test_shell_targets_alone_satisfy_the_trigger_check(tmp_path: Path) -> None:
    """A caller may derive ALL its paths, so shell-targets counts as a configured
    trigger — without this the no-trigger guard would red every such gate."""
    diff = _flood(tmp_path / "diff.txt", SHELL_CLOSURE_MEMBER)
    output = _run(tmp_path, SHELL_TARGETS=SHELL_TARGET, FAKE_DIFF_FILE=str(diff))
    assert "run=true" in output, output


def test_untracked_shell_target_fails_red(tmp_path: Path) -> None:
    """A closure that cannot be derived must red the decide job, never fall back
    to the regex alone."""
    diff = _flood(tmp_path / "diff.txt", SHELL_CLOSURE_MEMBER)
    r = _run_raw(
        tmp_path,
        PATHS_REGEX="^docs/",
        SHELL_TARGETS=".github/scripts/does-not-exist.sh",
        FAKE_DIFF_FILE=str(diff),
    )
    assert r.returncode != 0, r.stdout
    assert "shell run closure" in r.stderr


def test_both_closures_contribute_together(tmp_path: Path) -> None:
    """pytest-targets and shell-targets are a union: a member of either fires."""
    diff = _flood(tmp_path / "diff.txt", CLOSURE_MEMBER)
    output = _run(
        tmp_path,
        PYTEST_TARGETS=PYTEST_TARGET,
        SHELL_TARGETS=SHELL_TARGET,
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=true" in output, output


def test_regex_and_pytest_targets_both_contribute(tmp_path: Path) -> None:
    """Either source matching sets run=true; they are a union, not a choice."""
    by_regex = _flood(tmp_path / "d1.txt", ".claude/hooks/session-setup.sh")
    output = _run(
        tmp_path,
        PATHS_REGEX=r"^\.claude/",
        PYTEST_TARGETS=PYTEST_TARGET,
        FAKE_DIFF_FILE=str(by_regex),
    )
    assert "run=true" in output, output


# --- memo shadow ------------------------------------------------------------
# The gate's diff base can move forward to the last commit the work job actually
# passed on. The gate computes that verdict and LOGS it beside today's; nothing
# acts on it, so the cases below pin both halves: the shadow reports, and the
# gate's own answer is untouched by whatever the shadow says.

MEMO_JOB = "hook-lifecycle"
MEMO_WORKFLOW = "hook-lifecycle.yaml"
RUNS_PATH = f"repos/o/r/actions/workflows/{MEMO_WORKFLOW}/runs"


def _pr_touching_two_areas(repo: Path) -> tuple[str, str, str]:
    """A PR whose first commit touches .claude/hooks/ and whose second touches
    only docs/. Diffing base…head sees the hook; diffing verified…head does not —
    which is the whole saving the memo exists to take."""
    init_test_repo(repo)
    base = _commit(repo, "seed", "s", "base: fork point")
    git_out(repo, "switch", "-qc", "pr")
    verified = _commit(repo, ".claude/hooks/tool.sh", "t", "feat: touch a hook")
    return base, verified, _commit(repo, "docs/note.md", "n", "docs: a later push")


def _memo_env(tmp_path: Path, anchor: str | None, **overrides: str) -> dict[str, str]:
    """Env for a memo run whose stub `gh` reports one verified run at `anchor`."""
    started = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    runs = [] if anchor is None else [(1, anchor, started)]
    routes: list[tuple[str, object]] = [
        ("repos/o/r/actions/runs/1/jobs", run_jobs([(MEMO_JOB, "success")])),
        (RUNS_PATH, workflow_runs(runs)),
    ]
    bindir = tmp_path / "bin"
    write_gh_stub(bindir, routes)
    return {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "o/r",
        "WORKFLOW_REF": f"o/r/.github/workflows/{MEMO_WORKFLOW}@refs/heads/main",
        "HEAD_BRANCH": "pr",
        "BASE_REF": "main",
        "MEMO_ANCHOR_JOBS": f"^{MEMO_JOB}$",
        **overrides,
    }


def test_memo_shadow_reports_the_saving_without_taking_it(tmp_path: Path) -> None:
    """The later push changes nothing under the watched path, so the memo would
    skip while the gate still runs. Both verdicts must appear, and `run` must
    follow TODAY's diff — the shadow is worth nothing if it can move the gate."""
    repo = tmp_path / "r"
    base, verified, head = _pr_touching_two_areas(repo)
    result, output = _run_realgit(
        repo,
        **_memo_env(tmp_path, verified),
        BASE_SHA=base,
        HEAD_SHA=head,
        PATHS_REGEX=r"^\.claude/hooks/",
    )
    assert "run=true" in output, output
    assert f"memo-shadow: anchor={verified[:12]}" in result.stdout, result.stdout
    assert "would_run=false acting_run=true" in result.stdout, result.stdout


def test_memo_shadow_agrees_when_the_watched_path_changed_after_the_anchor(
    tmp_path: Path,
) -> None:
    """Anchoring at the fork point leaves the hook inside the memo range too, so
    both verdicts are true. Without this case the shadow could report `false`
    always and the case above would not notice."""
    repo = tmp_path / "r"
    base, _verified, head = _pr_touching_two_areas(repo)
    result, output = _run_realgit(
        repo,
        **_memo_env(tmp_path, base),
        BASE_SHA=base,
        HEAD_SHA=head,
        PATHS_REGEX=r"^\.claude/hooks/",
    )
    assert "run=true" in output, output
    assert "would_run=true acting_run=true" in result.stdout, result.stdout


def test_no_verified_run_reports_no_shadow(tmp_path: Path) -> None:
    """No anchor means no comparison to make. Printing a shadow computed from
    today's base would read as a parity result that nothing measured."""
    repo = tmp_path / "r"
    base, _verified, head = _pr_touching_two_areas(repo)
    result, output = _run_realgit(
        repo,
        **_memo_env(tmp_path, None),
        BASE_SHA=base,
        HEAD_SHA=head,
        PATHS_REGEX=r"^\.claude/hooks/",
    )
    assert "run=true" in output, output
    assert "memo-shadow" not in result.stdout, result.stdout


def test_the_gate_reports_no_shadow_without_an_anchor_pattern(tmp_path: Path) -> None:
    """A caller that did not opt in gets no shadow, so the memo costs it nothing."""
    repo = tmp_path / "r"
    base, verified, head = _pr_touching_two_areas(repo)
    result, output = _run_realgit(
        repo,
        **_memo_env(tmp_path, verified, MEMO_ANCHOR_JOBS=""),
        BASE_SHA=base,
        HEAD_SHA=head,
        PATHS_REGEX=r"^\.claude/hooks/",
    )
    assert "run=true" in output, output
    assert "memo-shadow" not in result.stdout, result.stdout


def test_memo_shadow_judges_comment_only_churn_over_the_memo_range(
    tmp_path: Path,
) -> None:
    """The anchor's commit changed a watched file substantively; the later push
    only reworded a comment in it. `diff-comment-only.sh` reads its range from the
    environment, so without scoping BASE_SHA to the anchor the shadow judges the
    branch-point range — where the substantive change still sits — and reports
    `would_run=true` for a memo range that is pure comment churn."""
    repo = tmp_path / "r"
    init_test_repo(repo)
    base = _commit(repo, "seed", "s", "base: fork point")
    git_out(repo, "switch", "-qc", "pr")
    rel = ".claude/hooks/pin.sh"
    verified = _commit(repo, rel, "# pin\nuv==0.12.0\n", "feat: bump the pin")
    head = _commit(repo, rel, "# pin the resolver\nuv==0.12.0\n", "docs: reword")

    result, output = _run_realgit(
        repo,
        **_memo_env(tmp_path, verified),
        BASE_SHA=base,
        HEAD_SHA=head,
        PATHS_REGEX=r"^\.claude/hooks/",
        IGNORE_COMMENT_ONLY="true",
    )
    assert "run=true" in output, output
    assert "would_run=false acting_run=true" in result.stdout, result.stdout


# --- an empty derivation, and the auth header the re-anchor fetch carries ----


def _empty_closure_python(tmp_path: Path) -> None:
    """A `python3` that exits 0 printing nothing, standing in for a closure script
    whose derivation succeeded but named no file."""
    stub = tmp_path / "python3"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)


def test_an_empty_derived_closure_fails_open(tmp_path: Path) -> None:
    """A closure script that exits 0 naming NO path is a derivation that failed.
    Reading it as "no watched file changed" skips the job the input exists to
    trigger and greens its required check, so the gate must run instead."""
    _empty_closure_python(tmp_path)
    diff = _flood(tmp_path / "diff.txt", CLOSURE_MEMBER)
    output = _run(
        tmp_path,
        PATHS_REGEX="^docs/",
        PYTEST_TARGETS=PYTEST_TARGET,
        FAKE_DIFF_FILE=str(diff),
    )
    assert "run=true" in output, output


def test_an_empty_derived_closure_is_what_flips_that_verdict(tmp_path: Path) -> None:
    """Non-vacuity for the case above: the SAME diff and regex with no targets
    configured still yields run=false, so the fail-open comes from the empty
    closure rather than from the diff matching something."""
    _empty_closure_python(tmp_path)
    diff = _flood(tmp_path / "diff.txt", CLOSURE_MEMBER)
    output = _run(tmp_path, PATHS_REGEX="^docs/", FAKE_DIFF_FILE=str(diff))
    assert "run=false" in output, output


def test_the_reanchor_fetch_scopes_its_auth_header_to_github(tmp_path: Path) -> None:
    """The token rides an http.extraheader on the live-base fetch. A BARE
    `http.extraheader` attaches it to every host this git process contacts, so a
    redirect or a submodule URL elsewhere would receive it — the header must name
    github.com."""
    repo = tmp_path / "r"
    stale_base, _live, head = _merge_conflict_pr(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "git-argv.log"
    real_git = shutil.which("git")
    assert real_git, "the fixture drives real git through the wrapper"
    wrapper = bindir / "git"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> {log}\nexec {real_git} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    _run_realgit_out(
        repo,
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        BASE_SHA=stale_base,
        HEAD_SHA=head,
        BASE_REF="main",
        GH_TOKEN="x",
        PATHS_REGEX=r"^\.claude/hooks/",
    )
    fetches = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if " fetch " in f" {line} "
    ]
    assert fetches, "the re-anchor fetch must have run, or this pins nothing"
    assert all("http.https://github.com/.extraheader=" in line for line in fetches)
    assert not any(re.search(r"(?<![.\w])http\.extraheader=", line) for line in fetches)
