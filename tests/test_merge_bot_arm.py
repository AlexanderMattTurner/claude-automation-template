"""merge-bot-arm.sh hands a pull request's merge to the merge-bot App.

The failure that matters is a PR left with auto-merge OFF: the script switches a
person's arming off before it arms as the App, so every exit after that point
must either leave the PR armed or go red.
"""

import json
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "merge-bot-arm.sh"
BOT = "merge-bot[bot]"

# A stub because GitHub's merge API needs a live repository and a write-scoped
# App token. It keeps the PR as a JSON file that `gh pr merge` rewrites the way
# GitHub does, and it refuses every call the script is not expected to make.
GH_STUB = r"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

here = Path(__file__).parent
world = json.loads((here / "world.json").read_text())
args = sys.argv[1:]
with (here / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")


def save():
    (here / "world.json").write_text(json.dumps(world))


if args[:1] == ["api"] and len(args) == 2:
    path = args[1]
    if path == "repos/o/r/pulls/7":
        print(json.dumps(world["pr"]))
    elif path == "repos/o/r":
        print(json.dumps(world["repo"]))
    elif path.startswith("repos/o/r/collaborators/") and path.endswith("/permission"):
        print(json.dumps({"permission": world["permissions"][path.split("/")[4]]}))
    else:
        sys.exit(f"fake gh: unmodelled path {path}")
elif args == ["pr", "merge", "7", "--disable-auto"]:
    if world["refuse"] == "disable":
        sys.exit("fake gh: Resource not accessible by integration")
    world["pr"]["auto_merge"] = None
    save()
elif args[:4] == ["pr", "merge", "7", "--auto"] and args[4] in ("--merge", "--squash", "--rebase"):
    if world["refuse"] == "arm":
        sys.exit("fake gh: Resource not accessible by integration")
    if world["clean"]:
        world["pr"].update(state="closed", merged=True)
    else:
        world["pr"]["auto_merge"] = {
            "enabled_by": {"login": "merge-bot[bot]"},
            "merge_method": args[4][2:],
        }
    save()
else:
    sys.exit(f"fake gh: unmodelled call {args}")
"""


def armed(login: str, method: str = "squash", title=None, message=None) -> dict:
    return {
        "enabled_by": {"login": login},
        "merge_method": method,
        "commit_title": title,
        "commit_message": message,
    }


def run(
    tmp_path: Path,
    action: str,
    *,
    auto_merge=None,
    state="open",
    refuse=None,
    clean=False,
    repo=None,
    sender="owner",
):
    """Run the script against a fake PR #7. Returns (result, merge calls, PR)."""
    world = {
        "pr": {"state": state, "merged": False, "auto_merge": auto_merge},
        "repo": repo or {"allow_merge_commit": True, "allow_squash_merge": True},
        "permissions": {
            "owner": "admin",
            "writer": "write",
            "triager": "read",
            "outsider": "none",
        },
        "refuse": refuse,
        "clean": clean,
    }
    (tmp_path / "world.json").write_text(json.dumps(world), encoding="utf-8")
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)
    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
            "GH_TOKEN": "x",
            "GH_REPO": "o/r",
            "PR_NUMBER": "7",
            "BOT_LOGIN": BOT,
            "EVENT_ACTION": action,
            "SENDER": sender,
        },
    )
    log = tmp_path / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    merges = [c[3:] for c in calls if c[:2] == ["pr", "merge"]]
    pr = json.loads((tmp_path / "world.json").read_text(encoding="utf-8"))["pr"]
    return res, merges, pr


@pytest.mark.parametrize(
    ("title", "message", "extra"),
    [
        ("feat: x (#7)", "body", ["--subject", "feat: x (#7)", "--body", "body"]),
        ("feat: x (#7)", None, ["--subject", "feat: x (#7)"]),
        (None, "body", ["--body", "body"]),
        # A cleared body is a choice, and differs from GitHub's default body.
        ("feat: x (#7)", "", ["--subject", "feat: x (#7)", "--body", ""]),
        (None, None, []),
    ],
)
def test_a_person_arming_auto_merge_is_taken_over_with_their_choices(
    tmp_path: Path, title, message, extra
):
    res, merges, pr = run(
        tmp_path,
        "auto_merge_enabled",
        auto_merge=armed("alice", "squash", title=title, message=message),
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert merges == [["--disable-auto"], ["--auto", "--squash", *extra]]
    assert pr["auto_merge"]["enabled_by"]["login"] == BOT


@pytest.mark.parametrize(
    ("action", "auto_merge", "state"),
    [
        # The App's own arming is left alone, or the two events would loop.
        ("auto_merge_enabled", armed(BOT), "open"),
        ("labeled", armed(BOT), "open"),
        # The person switched auto-merge off again before the job started.
        ("auto_merge_enabled", None, "open"),
        # The PR merged or closed before the job started.
        ("auto_merge_enabled", armed("alice"), "closed"),
    ],
)
def test_nothing_to_arm_touches_nothing(tmp_path: Path, action, auto_merge, state):
    res, merges, pr = run(tmp_path, action, auto_merge=auto_merge, state=state)
    assert res.returncode == 0, res.stdout + res.stderr
    assert merges == []
    assert pr["auto_merge"] == auto_merge


@pytest.mark.parametrize(
    ("repo", "flag"),
    [
        ({"allow_merge_commit": True, "allow_squash_merge": True}, "--merge"),
        ({"allow_merge_commit": False, "allow_squash_merge": True}, "--squash"),
        ({"allow_merge_commit": False, "allow_squash_merge": False}, "--rebase"),
    ],
)
def test_the_label_arms_an_unarmed_pr_with_a_method_the_repo_allows(
    tmp_path: Path, repo, flag
):
    res, merges, pr = run(tmp_path, "labeled", repo=repo, sender="writer")
    assert res.returncode == 0, res.stdout + res.stderr
    assert merges == [["--auto", flag]]
    assert pr["auto_merge"]["enabled_by"]["login"] == BOT


@pytest.mark.parametrize(
    ("sender", "access"), [("triager", "read"), ("outsider", "none")]
)
def test_a_label_from_someone_who_cannot_merge_arms_nothing(
    tmp_path: Path, sender, access
):
    res, merges, pr = run(tmp_path, "labeled", sender=sender)
    assert res.returncode == 1
    assert merges == []
    assert pr["auto_merge"] is None
    assert f"{sender} has '{access}' access" in res.stdout


def test_any_label_sender_may_hand_over_what_a_person_already_armed(tmp_path: Path):
    res, merges, _ = run(
        tmp_path, "labeled", auto_merge=armed("alice", "rebase"), sender="triager"
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert merges == [["--disable-auto"], ["--auto", "--rebase"]]


def test_a_pr_that_merges_at_once_counts_as_landed(tmp_path: Path):
    res, merges, pr = run(tmp_path, "labeled", clean=True)
    assert res.returncode == 0, res.stdout + res.stderr
    assert merges == [["--auto", "--merge"]]
    assert pr["merged"] is True


def test_a_refused_disable_leaves_the_persons_arming_in_place(tmp_path: Path):
    before = armed("alice")
    res, merges, pr = run(
        tmp_path, "auto_merge_enabled", auto_merge=before, refuse="disable"
    )
    assert res.returncode == 1
    assert merges == [["--disable-auto"]]
    assert pr["auto_merge"] == before


def test_a_refused_re_arm_goes_red_and_says_auto_merge_is_off(tmp_path: Path):
    res, merges, pr = run(
        tmp_path, "auto_merge_enabled", auto_merge=armed("alice"), refuse="arm"
    )
    assert res.returncode == 1
    assert merges == [["--disable-auto"], ["--auto", "--squash"]]
    assert pr["auto_merge"] is None
    assert "alice had armed its auto-merge, which is now OFF" in res.stdout


def test_a_refused_arm_from_scratch_does_not_claim_auto_merge_was_switched_off(
    tmp_path: Path,
):
    res, merges, _ = run(tmp_path, "labeled", refuse="arm")
    assert res.returncode == 1
    assert merges == [["--auto", "--merge"]]
    assert "now OFF" not in res.stdout
