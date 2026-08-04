"""The merge-delta detector is what makes unattended conflict resolution safe.

A merge commit's tree is authored freely, so a resolution can introduce content
present in neither parent, and no ordinary one-parent diff shows it. These cases
drive real git repositories rather than stubbing git, because the whole question
is what `--remerge-diff` reports about real trees.

The two failure directions are not symmetric. A FALSE NEGATIVE — an invented
line the report omits — is the one that costs a merge, so the evil-merge case is
the load-bearing assertion here. A false positive only costs a human a read.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    / ".github"
    / "scripts"
    / "remerge-diff-report.py"
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def commit(repo: Path, path: str, text: str, message: str) -> str:
    (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    return r


def report(repo: Path, base: str, head: str, **env: str) -> str:
    res = subprocess.run(
        ["python3", str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "BASE_SHA": base,
            "HEAD_SHA": head,
            **env,
        },
    )
    return res.stdout


def conflicting_merge(repo: Path, ours: str, theirs: str) -> tuple[str, str]:
    """Build two branches that conflict on `f.txt`, leaving the merge in
    progress. Returns (base_sha, merge_head_ref)."""
    base = commit(repo, "f.txt", "one\ntwo\nthree\n", "base")
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "f.txt", theirs, "side change")
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", ours, "main change")
    res = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-edit", "side"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0, "fixture must actually conflict"
    return base, "side"


def test_an_invented_line_is_reported(repo: Path):
    # The resolution keeps both sides AND adds a line neither parent ever had.
    # This is the evil merge. Missing it is the failure that costs a merge.
    base, _ = conflicting_merge(repo, "one\nOURS\nthree\n", "one\nTHEIRS\nthree\n")
    (repo / "f.txt").write_text("one\nOURS\nTHEIRS\nINVENTED\nthree\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    head = git(repo, "rev-parse", "HEAD").strip()

    out = report(repo, base, head)
    assert "INVENTED" in out, "the detector missed content present in neither parent"
    assert "Hand-authored merge-resolution deltas" in out


def test_an_ordinary_resolution_taking_both_sides_is_retired(repo: Path):
    # Both sides' own lines, nothing else. Every block traces to a parent, so
    # nothing needs a human — this is the false-positive direction.
    base, _ = conflicting_merge(repo, "one\nOURS\nthree\n", "one\nTHEIRS\nthree\n")
    (repo / "f.txt").write_text("one\nOURS\nTHEIRS\nthree\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    head = git(repo, "rev-parse", "HEAD").strip()

    assert report(repo, base, head).strip() == ""


def test_a_resolution_corrected_by_a_later_commit_is_retired(repo: Path):
    # A pushed merge's remerge-diff never changes, so a follow-up commit is the
    # only correction available. Without this the corrected resolution could
    # never clear, and the report would nag forever.
    base, _ = conflicting_merge(repo, "one\nOURS\nthree\n", "one\nTHEIRS\nthree\n")
    (repo / "f.txt").write_text("one\nOURS\nTHEIRS\nINVENTED\nthree\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    merge = git(repo, "rev-parse", "HEAD").strip()

    flagged = report(repo, base, merge)
    assert "INVENTED" in flagged, "precondition: it must be flagged before the fix"

    commit(repo, "f.txt", "one\nOURS\nTHEIRS\nthree\n", "drop the invented line")
    head = git(repo, "rev-parse", "HEAD").strip()
    assert report(repo, base, head).strip() == ""


def test_a_deletion_the_resolution_made_alone_is_reported(repo: Path):
    # The directional half of the trace: a line BOTH parents still carry, which
    # the resolution dropped. Base count is not greater than the parents', so it
    # must stay under review. This is a guardrail silently removed via a merge.
    base = commit(repo, "f.txt", "keep\nGUARD\ntail\n", "base")
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "f.txt", "keep\nGUARD\ntail\nside\n", "side appends")
    git(repo, "checkout", "-q", "main")
    commit(repo, "f.txt", "keep\nGUARD\ntail\nmain\n", "main appends")
    res = subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-edit", "side"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    # Resolve, but silently drop GUARD — which neither side touched.
    (repo / "f.txt").write_text("keep\ntail\nside\nmain\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    head = git(repo, "rev-parse", "HEAD").strip()

    out = report(repo, base, head)
    assert "GUARD" in out, "a line neither parent removed was dropped and not reported"


def test_the_provenance_block_names_both_sides(repo: Path):
    # The downstream reviewer has no shell and cannot read the parents, so
    # without this block a deliberate removal and a dropped line are identical.
    base, _ = conflicting_merge(repo, "one\nOURS\nthree\n", "one\nTHEIRS\nthree\n")
    (repo / "f.txt").write_text("one\nOURS\nTHEIRS\nINVENTED\nthree\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    head = git(repo, "rev-parse", "HEAD").strip()

    out = report(repo, base, head)
    assert "Which side changed each file" in out
    assert "parent 1:" in out and "parent 2:" in out
    assert "main change" in out and "side change" in out


def test_shas_out_lists_only_the_merges_that_survived(repo: Path, tmp_path: Path):
    base, _ = conflicting_merge(repo, "one\nOURS\nthree\n", "one\nTHEIRS\nthree\n")
    (repo / "f.txt").write_text("one\nOURS\nTHEIRS\nINVENTED\nthree\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    head = git(repo, "rev-parse", "HEAD").strip()

    out_file = tmp_path / "shas.txt"
    subprocess.run(
        ["python3", str(SCRIPT), "--shas-out", str(out_file)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "BASE_SHA": base,
            "HEAD_SHA": head,
        },
    )
    assert out_file.read_text().split() == [head]


def test_an_octopus_merge_fails_loud(repo: Path):
    # Skipping it silently would report "nothing to review" about exactly the
    # commit shape that cannot be reconstructed.
    base = commit(repo, "f.txt", "base\n", "base")
    for name in ("a", "b"):
        git(repo, "checkout", "-q", "-b", name, base)
        commit(repo, f"{name}.txt", name, f"{name} file")
    git(repo, "checkout", "-q", "main")
    # main needs a commit of its own, or `git merge a b` fast-forwards to `a`
    # first and lands a two-parent merge instead of an octopus.
    commit(repo, "main.txt", "main", "main file")
    git(repo, "merge", "--no-edit", "-q", "a", "b")
    head = git(repo, "rev-parse", "HEAD").strip()
    assert len(git(repo, "rev-list", "--parents", "-n1", head).split()) == 4

    res = subprocess.run(
        ["python3", str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "BASE_SHA": base,
            "HEAD_SHA": head,
        },
    )
    assert res.returncode != 0
    assert "octopus" in res.stderr


def test_the_cap_is_off_unless_asked_for(repo: Path):
    # The readers that audit have no size limit; only the PR comment does. A
    # merge dropped from what they read is a merge nobody looks at.
    base, _ = conflicting_merge(repo, "one\nOURS\nthree\n", "one\nTHEIRS\nthree\n")
    (repo / "f.txt").write_text("one\nOURS\nTHEIRS\nINVENTED\nthree\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-edit")
    head = git(repo, "rev-parse", "HEAD").strip()

    assert "INVENTED" in report(repo, base, head)
    capped = report(repo, base, head, REMERGE_REPORT_MAX_BYTES="200")
    assert "INVENTED" not in capped and "omitted" in capped
