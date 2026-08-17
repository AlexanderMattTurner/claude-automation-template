"""Behavioral tests for .github/scripts/lib-path-match.sh.

Every helper here answers one question for a path gate: did anything watched
change? A gate that reads a TOOL FAILURE as "no" skips its expensive job, and the
job's always() reporter greens the skip — a required check that verified nothing.
So each helper must fail OPEN (report the widest match) on anything except a
clean "grep looked and found no line".

The cases below drive the helpers through real bash, with a stub `grep` on PATH
for the failure arms, because grep's exit 2 is not reachable from a well-formed
call.
"""

import os
from pathlib import Path

from tests._helpers import REPO_ROOT, run_capture

LIB = REPO_ROOT / ".github" / "scripts" / "lib-path-match.sh"

CHANGED = "src/app.js\ntests/_fake_gh.py\ndocs/readme.md"


def _bash(
    snippet: str, *args: str, path_prepend: Path | None = None
) -> tuple[int, str]:
    """Source the lib and run `snippet`; returns (exit status, stdout)."""
    env = {**os.environ}
    if path_prepend is not None:
        env["PATH"] = f"{path_prepend}{os.pathsep}{env['PATH']}"
    res = run_capture(["bash", "-c", f'. "{LIB}"\n{snippet}', "bash", *args], env=env)
    assert "not found" not in res.stderr, res.stderr
    return res.returncode, res.stdout


def _failing_grep(tmp_path: Path) -> Path:
    """A `grep` that exits 2 — grep's own "I failed", not "no line matched"."""
    stub = tmp_path / "grep"
    stub.write_text("#!/usr/bin/env bash\nexit 2\n", encoding="utf-8")
    stub.chmod(0o755)
    return tmp_path


# --- path_gate_matches ------------------------------------------------------


def test_matches_reports_a_hit() -> None:
    assert _bash('path_gate_matches "$1" "$2"', r"^src/", CHANGED)[0] == 0


def test_matches_reports_a_clean_no_match() -> None:
    assert _bash('path_gate_matches "$1" "$2"', r"^nope/", CHANGED)[0] == 1


def test_matches_fails_open_when_grep_fails(tmp_path: Path) -> None:
    status, _ = _bash(
        'path_gate_matches "$1" "$2"',
        r"^nope/",
        CHANGED,
        path_prepend=_failing_grep(tmp_path),
    )
    assert status == 0, "a grep failure must read as a match, never as no-match"


# --- path_gate_matching_lines -----------------------------------------------


def test_matching_lines_prints_only_the_matches() -> None:
    status, out = _bash('path_gate_matching_lines "$1" "$2"', r"^src/", CHANGED)
    assert (status, out) == (0, "src/app.js\n")


def test_matching_lines_prints_nothing_on_a_clean_no_match() -> None:
    assert _bash('path_gate_matching_lines "$1" "$2"', r"^nope/", CHANGED) == (0, "")


def test_matching_lines_fails_open_to_every_line_when_grep_fails(
    tmp_path: Path,
) -> None:
    status, out = _bash(
        'path_gate_matching_lines "$1" "$2"',
        r"^nope/",
        CHANGED,
        path_prepend=_failing_grep(tmp_path),
    )
    assert (status, out) == (0, CHANGED + "\n")


# --- path_gate_matching_members ---------------------------------------------

MEMBERS = "tests/_fake_gh.py\ntests/conftest.py"


def test_matching_members_prints_the_exact_members() -> None:
    status, out = _bash('path_gate_matching_members "$1" "$2"', MEMBERS, CHANGED)
    assert (status, out) == (0, "tests/_fake_gh.py\n")


def test_matching_members_is_whole_line_not_a_prefix() -> None:
    """A longer path sharing a member's prefix is not a member: a substring match
    would over-run every gate that derives its watched paths."""
    changed = "tests/_fake_gh.py.orig"
    assert _bash('path_gate_matching_members "$1" "$2"', MEMBERS, changed) == (0, "")


def test_matching_members_prints_nothing_on_a_clean_no_match() -> None:
    assert _bash('path_gate_matching_members "$1" "$2"', "docs/nope.md", CHANGED) == (
        0,
        "",
    )


def test_matching_members_fails_open_to_every_line_when_grep_fails(
    tmp_path: Path,
) -> None:
    status, out = _bash(
        'path_gate_matching_members "$1" "$2"',
        MEMBERS,
        CHANGED,
        path_prepend=_failing_grep(tmp_path),
    )
    assert (status, out) == (0, CHANGED + "\n")


def test_matching_members_fails_open_on_an_empty_list() -> None:
    """A derivation that produced nothing is a derivation that failed. Reading it
    as "no member changed" is the silent skip this file exists to prevent."""
    status, out = _bash('path_gate_matching_members "$1" "$2"', "", CHANGED)
    assert (status, out) == (0, CHANGED + "\n")


def test_matching_members_fails_open_on_a_blank_only_list() -> None:
    """The shape a real caller produces: a closure script that exits 0 printing
    nothing leaves the accumulator holding only its separator newlines."""
    status, out = _bash('path_gate_matching_members "$1" "$2"', "\n\n", CHANGED)
    assert (status, out) == (0, CHANGED + "\n")


def test_the_replaced_idiom_would_have_matched_nothing() -> None:
    """Non-vacuity for the two arms above: the inline `grep -xFf … || true` these
    helpers replace answers "nothing matched" for BOTH an empty list and a failed
    grep — the false green, reproduced here against real grep so the assertions
    above pin a behavior change rather than a restatement."""
    status, out = _bash(
        'grep -xFf <(printf \'%s\\n\' "$1") <<<"$2" || true', "\n\n", CHANGED
    )
    assert (status, out) == (0, "")
