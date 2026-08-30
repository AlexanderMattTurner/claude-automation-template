"""No file on the `template-sync` branch may carry conflict markers.

template-sync.sh writes diff3 markers into the tree deliberately, and
create-pull-request commits that tree before either resolver tier runs. So every
file a tier fails to settle reaches the branch with `<<<<<<< local` still in it,
and until this gate nothing failed: the resolve driver printed a `::warning::`
for its `unresolved` set and exited 0. Downstream that shipped a bash library
whose markers are a syntax error (agent-sanitizer#305,
agent-control-plane-core#52).

Each case drives the real script against a real git remote and asserts on the
branch the remote ends up holding, because the branch is what a consumer checks
out.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests._helpers import (
    REPO_ROOT,
    commit_all,
    commit_files,
    git_env,
    git_out,
    init_test_repo,
)

SCRIPT = REPO_ROOT / ".github" / "scripts" / "template-sync-marker-gate.sh"

MARKED = (
    "retry_with_backoff() {\n"
    "<<<<<<< local\n"
    "  local tries=5\n"
    "||||||| base\n"
    "  local tries=2\n"
    "=======\n"
    "  local tries=3\n"
    ">>>>>>> template\n"
    "}\n"
)
LOCAL = "retry_with_backoff() {\n  local tries=5\n}\n"
# A repository may keep marker text on purpose. This one documents what a
# conflict looks like, and the gate must never withhold it.
FIXTURE = "docs/what-a-conflict-looks-like.md"


@pytest.fixture
def sandbox(tmp_path: Path) -> tuple[Path, str]:
    """A work repo with a bare `origin`, `main` holding the pre-sync copies, and
    `template-sync` checked out. Returns (work, base_sha)."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main"], cwd=origin, check=True
    )

    work = tmp_path / "work"
    init_test_repo(work)
    base_sha = commit_files(
        work,
        {
            ".github/scripts/lib/retry.bash": LOCAL,
            "README.md": "# repo\n",
            FIXTURE: MARKED,
        },
        "pre-sync",
    )
    env = git_env()
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)], cwd=work, check=True
    )
    subprocess.run(
        ["git", "push", "-q", "origin", "main"], cwd=work, env=env, check=True
    )
    subprocess.run(
        ["git", "checkout", "-q", "-b", "template-sync"], cwd=work, check=True
    )
    return work, base_sha


def fetch_origin(work: Path) -> None:
    subprocess.run(
        ["git", "fetch", "-q", "origin"], cwd=work, env=git_env(), check=True
    )


def push_branch(work: Path) -> None:
    subprocess.run(
        ["git", "push", "-q", "origin", "template-sync"],
        cwd=work,
        env=git_env(),
        check=True,
    )


def run_gate(
    work: Path,
    base_sha: str,
    script: Path = SCRIPT,
    extra_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**git_env(), "BASE_SHA": base_sha, "GITHUB_TOKEN": "x"}
    if extra_path is not None:
        env["PATH"] = f"{extra_path}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(script)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def pushed(work: Path, path: str) -> str:
    """The content the remote's `template-sync` tip holds for PATH."""
    fetch_origin(work)
    return git_out(work, "show", f"origin/template-sync:{path}")


def test_a_marked_file_is_withheld_and_the_run_goes_red(sandbox):
    work, base_sha = sandbox
    commit_files(
        work,
        {".github/scripts/lib/retry.bash": MARKED, "README.md": "# repo v2\n"},
        "sync from template",
    )
    push_branch(work)

    result = run_gate(work, base_sha)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::" in result.stdout, "the run must carry a legible red"
    assert ".github/scripts/lib/retry.bash" in result.stdout
    # The conflicted file goes back to what this repo had; every other synced
    # change on the branch survives, so the gate withholds one file, not the sync.
    assert pushed(work, ".github/scripts/lib/retry.bash") == LOCAL.rstrip("\n")
    assert pushed(work, "README.md") == "# repo v2"


def test_a_file_the_base_does_not_have_is_removed(sandbox):
    """A tier can only create a marked path that the pre-sync repo never had.
    There is no earlier content to restore, so the branch must lose the file."""
    work, base_sha = sandbox
    commit_files(work, {"new-from-template.bash": MARKED}, "sync from template")
    push_branch(work)

    result = run_gate(work, base_sha)

    assert result.returncode == 1, result.stdout + result.stderr
    tracked = git_out(work, "ls-tree", "-r", "--name-only", "origin/template-sync")
    assert "new-from-template.bash" not in tracked.splitlines()


def test_an_uncommitted_marked_edit_is_not_the_branch(sandbox):
    """The resolve step leaves edits it chose not to push. A consumer fetches the
    branch, so the workspace must not decide the verdict."""
    work, base_sha = sandbox
    tip = commit_files(work, {"README.md": "# repo v2\n"}, "sync from template")
    push_branch(work)
    (work / ".github" / "scripts" / "lib" / "retry.bash").write_text(
        MARKED, encoding="utf-8"
    )

    result = run_gate(work, base_sha)

    assert result.returncode == 0, result.stdout + result.stderr
    fetch_origin(work)
    assert git_out(work, "rev-parse", "origin/template-sync") == tip


def test_marker_text_the_repo_already_had_is_left_alone(sandbox):
    """A file whose pre-sync copy carries markers is the repository's own
    fixture, not a failed resolution, so the gate must pass it."""
    work, base_sha = sandbox
    tip = commit_files(work, {"README.md": "# repo v2\n"}, "sync from template")
    push_branch(work)

    result = run_gate(work, base_sha)

    assert result.returncode == 0, result.stdout + result.stderr
    assert pushed(work, FIXTURE) == MARKED.rstrip("\n")
    assert git_out(work, "rev-parse", "origin/template-sync") == tip


def test_a_base_the_checkout_cannot_resolve_stops_the_gate(sandbox):
    """BASE_SHA says what each marked file goes back to, and a path it cannot
    resolve is deleted. An unreadable base must stop the gate, not empty it."""
    work, _ = sandbox
    tip = commit_files(
        work,
        {".github/scripts/lib/retry.bash": MARKED},
        "sync from template",
    )
    push_branch(work)

    result = run_gate(work, "0" * 40)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "is not a commit" in result.stdout
    fetch_origin(work)
    assert git_out(work, "rev-parse", "origin/template-sync") == tip


def test_a_failed_scan_never_reads_as_a_clean_branch(sandbox, tmp_path):
    """The scan is the gate's only evidence. A `git grep` that dies must reach
    the caller as a failure, not as an empty list that looks like no markers."""
    work, base_sha = sandbox
    commit_files(work, {".github/scripts/lib/retry.bash": MARKED}, "sync from template")
    push_branch(work)
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git, "the stub forwards to the real git; without it this proves nothing"
    (stub_dir / "git").write_text(
        f'#!/usr/bin/env bash\n[[ "$1" == "grep" ]] && exit 128\nexec {real_git} "$@"\n',
        encoding="utf-8",
    )
    (stub_dir / "git").chmod(0o755)

    result = run_gate(work, base_sha, extra_path=stub_dir)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "the marker scan failed" in result.stdout
    assert "no conflict markers" not in result.stdout


def test_the_gate_runs_from_a_base_copy_when_its_own_file_is_marked(sandbox, tmp_path):
    """`.github/scripts` is in SYNC_PATHS, so a sync can leave markers in the
    gate's own file. The workflow runs a BASE_SHA copy for exactly this case."""
    work, base_sha = sandbox
    gate_path = ".github/scripts/template-sync-marker-gate.sh"
    commit_files(work, {gate_path: MARKED}, "sync from template")
    push_branch(work)

    in_tree = run_gate(work, base_sha, script=work / gate_path)
    assert in_tree.returncode != 0
    assert "::error::" not in in_tree.stdout, "bash cannot parse the marked copy"

    base_copy = tmp_path / "gate" / ".github" / "scripts"
    base_copy.mkdir(parents=True)
    shutil.copy2(SCRIPT, base_copy / SCRIPT.name)
    shutil.copytree(SCRIPT.parent / "lib", base_copy / "lib")

    result = run_gate(work, base_sha, script=base_copy / SCRIPT.name)

    assert result.returncode == 1, result.stdout + result.stderr
    tracked = git_out(work, "ls-tree", "-r", "--name-only", "origin/template-sync")
    assert gate_path not in tracked.splitlines()


def test_a_branch_with_no_marker_text_at_all_is_clean(sandbox):
    """`git grep` exits 1 when nothing matches, which is the ordinary result on
    a clean sync. That is the scan's answer, not the scan failing — and it must
    read that way to a caller that invokes the scan unprotected too, because the
    contract cannot depend on the caller's syntax."""
    work, base_sha = sandbox
    subprocess.run(["git", "rm", "-q", FIXTURE], cwd=work, env=git_env(), check=True)
    commit_all(work, "sync from template")
    push_branch(work)

    # HEAD is the branch here, so the scan matches nothing. Run it BEFORE the
    # gate, which leaves the workspace back on a base that does carry markers.
    lib = SCRIPT.parent / "lib" / "merge-conflict.bash"
    unprotected = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail\nsource "{lib}"\n'
            f'committed_marker_paths "{base_sha}"\necho survived\n',
        ],
        cwd=work,
        env=git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert unprotected.returncode == 0, unprotected.stdout + unprotected.stderr
    assert "survived" in unprotected.stdout

    result = run_gate(work, base_sha)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no conflict markers" in result.stdout


def test_a_clean_run_leaves_the_workspace_off_the_sync_branch(sandbox):
    """The next workflow step resolves its own script from the workspace, and
    the sync branch is the template's proposal rather than a reviewed tree."""
    work, base_sha = sandbox
    commit_files(work, {"README.md": "# repo v2\n"}, "sync from template")
    push_branch(work)

    result = run_gate(work, base_sha)

    assert result.returncode == 0, result.stdout + result.stderr
    assert git_out(work, "rev-parse", "HEAD") == base_sha


def test_a_clean_branch_passes_and_is_left_alone(sandbox):
    work, base_sha = sandbox
    tip = commit_files(
        work,
        {".github/scripts/lib/retry.bash": "  local tries=4\n"},
        "sync from template",
    )
    push_branch(work)

    result = run_gate(work, base_sha)

    assert result.returncode == 0, result.stdout + result.stderr
    fetch_origin(work)
    assert git_out(work, "rev-parse", "origin/template-sync") == tip
