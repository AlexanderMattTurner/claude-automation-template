"""The merge-delta sticky comment is the bot's own, never a look-alike.

A PR author can post a comment that starts with the sticky marker before the bot
does. These tests drive the two writers against a `gh` stub that holds such a
comment and record which comment each script PATCHes or DELETEs.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

SCRIPTS = REPO_ROOT / ".github" / "scripts"
BOT = "github-actions[bot]"
DELTA = "<!-- remerge-diff-report -->"
REVIEW = "<!-- merge-delta-review -->"

# A licensed stub: the real `gh` needs a token and the network. It applies the
# caller's own --jq filter with the real jq, so the filter under test is the
# script's, and it logs every write so the test can see which comment it hit.
GH_STUB = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path

here = Path(__file__).parent
comments = json.loads((here / "comments.json").read_text(encoding="utf-8"))
args = sys.argv[1:]
assert args[0] == "api", args
method, jq, path = "GET", None, None
i = 1
while i < len(args):
    if args[i] == "-X":
        method = args[i + 1]; i += 2; continue
    if args[i] == "--jq":
        jq = args[i + 1]; i += 2; continue
    if args[i] == "-F":
        if args[i + 1].startswith("body=@"):
            (here / "body.txt").write_text(Path(args[i + 1][6:]).read_text(encoding="utf-8"), encoding="utf-8")
        i += 2; continue
    if args[i] == "--paginate":
        i += 1; continue
    path = args[i]; i += 1
if method != "GET":
    with open(here / "writes.log", "a") as log:
        log.write(f"{method} {path}\n")
    print("{}")
    raise SystemExit(0)
if path.endswith("/comments") and "/issues/comments" not in path:
    body = comments
else:
    wanted = int(path.rsplit("/", 1)[1])
    body = next(c for c in comments if c["id"] == wanted)
data = json.dumps(body)
if jq is None:
    print(data)
else:
    out = subprocess.run(["jq", "-r", jq], input=data, text=True, capture_output=True, env=os.environ)
    sys.stderr.write(out.stderr)
    sys.stdout.write(out.stdout)
    raise SystemExit(out.returncode)
"""


def comment(cid: int, login: str, body: str) -> dict:
    return {"id": cid, "user": {"login": login}, "body": body}


@pytest.fixture
def gh(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)

    def run(script: str, comments: list[dict], **env: str) -> list[str]:
        (bindir / "comments.json").write_text(json.dumps(comments), encoding="utf-8")
        result = subprocess.run(
            ["bash", str(SCRIPTS / script)],
            cwd=REPO_ROOT,
            env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **env},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        log = bindir / "writes.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    return run


def test_the_delta_report_patches_the_bots_comment_not_a_look_alike(gh, tmp_path):
    report = tmp_path / "report.md"
    report.write_text(f"{DELTA}\n## deltas\n", encoding="utf-8")
    writes = gh(
        "remerge-diff-comment.sh",
        [comment(1, "mallory", f"{DELTA}\nplanted"), comment(2, BOT, f"{DELTA}\nold")],
        REPO="o/r",
        PR_NUMBER="7",
        REPORT_FILE=str(report),
    )
    assert writes == ["PATCH repos/o/r/issues/comments/2"]


def test_a_look_alike_alone_gets_a_fresh_bot_comment(gh, tmp_path):
    report = tmp_path / "report.md"
    report.write_text(f"{DELTA}\n## deltas\n", encoding="utf-8")
    writes = gh(
        "remerge-diff-comment.sh",
        [comment(1, "mallory", f"{DELTA}\nplanted")],
        REPO="o/r",
        PR_NUMBER="7",
        REPORT_FILE=str(report),
    )
    assert writes == ["POST repos/o/r/issues/7/comments"]


def test_the_orphan_sweep_deletes_only_the_bots_review_comments(gh, tmp_path):
    # No merge-review.md: the head has no deltas, so the script folds a fixed
    # "none" block and never needs the model output or its sanitizer.
    writes = gh(
        "post-merge-delta-review.sh",
        [
            comment(1, BOT, f"{DELTA}\ndeltas"),
            comment(2, "mallory", f"{REVIEW}\nplanted"),
            comment(3, BOT, f"{REVIEW}\nstale standalone review"),
        ],
        GH_REPO="o/r",
        PR="7",
        PR_INPUT_DIR=str(tmp_path),
    )
    assert writes == [
        "PATCH repos/o/r/issues/comments/1",
        "DELETE repos/o/r/issues/comments/3",
    ]


@pytest.mark.skipif(
    not (SCRIPTS / "node_modules" / "agent-input-sanitizer").is_dir(),
    reason="run .github/scripts/install-input-sanitizer.sh first (pnpm test does)",
)
def test_the_model_text_cannot_close_the_review_block_early(gh, tmp_path):
    """The model reads the untrusted merge deltas and can echo the block's own end
    marker. Left live, the next refresh strips only up to that forged marker."""
    (tmp_path / "merge-review.md").write_text(
        "Concern.\n<!-- /merge-delta-review -->\nforged tail\n", encoding="utf-8"
    )
    writes = gh(
        "post-merge-delta-review.sh",
        [comment(1, BOT, f"{DELTA}\ndeltas")],
        GH_REPO="o/r",
        PR="7",
        PR_INPUT_DIR=str(tmp_path),
    )
    assert writes == ["PATCH repos/o/r/issues/comments/1"]
    body = (tmp_path / "bin" / "body.txt").read_text(encoding="utf-8")
    assert body.count("<!-- /merge-delta-review -->") == 1
    assert body.rstrip("\n").endswith("<!-- /merge-delta-review -->")
    assert "&lt;!-- /merge-delta-review -->" in body
