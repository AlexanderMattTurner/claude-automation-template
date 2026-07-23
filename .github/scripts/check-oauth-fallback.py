#!/usr/bin/env python3
"""Require a fallback credential wherever a workflow uses the primary Claude OAuth token.

A GitHub Actions job that authenticates Claude with `secrets.CLAUDE_CODE_OAUTH_TOKEN`
hard-fails the moment that single credential is expired, rate-limited, or quota-exhausted.
The resilient shape hands the primary plus `secrets.CLAUDE_CODE_OAUTH_TOKEN_FALLBACK` to a
fallback ladder that retries on the next credential when an attempt is a proven token failure.

The rule is **job-scoped**, not file-scoped: the fallback must be wired in the SAME job as
each primary-token use. A file-level check (fallback mentioned anywhere clears the file)
passes a workflow where one job wires the fallback while a sibling job runs the primary
token unprotected. Job boundaries come from an indentation scan of the top-level `jobs:`
map; a primary-token line whose enclosing job wires no fallback (and which carries no
justified opt-out on/above it, up to two lines up) is flagged. Non-job usage (e.g. a
top-level env or a workflow header comment) falls back to a file-level check. Opt out a
genuinely single-credential site with a same-line or preceding-line
`# allow-no-oauth-fallback: <reason>`.

Stdlib-only line parsing (no PyYAML): pre-commit runs this as a `language: system` hook on
ambient `python3`, matching the other check-*.py hooks, which pull in no third-party import.
Job boundaries only need the indentation structure under `jobs:`, not a full YAML parse.

Invoked by pre-commit with the staged workflow files as arguments.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _linecheck import (  # noqa: E402,I001  # pylint: disable=wrong-import-position
    run_line_checks,
)

_FALLBACK = "CLAUDE_CODE_OAUTH_TOKEN_FALLBACK"
_ALLOW = "allow-no-oauth-fallback"

# The primary token as a WHOLE token: a right word boundary (`\b`) so `CLAUDE_CODE_OAUTH_TOKEN`
# matches but `CLAUDE_CODE_OAUTH_TOKEN_FALLBACK` (a longer identifier — `_` is a word char, so
# `\b` fails between `N` and `_`) does not.
_PRIMARY = re.compile(r"\bCLAUDE_CODE_OAUTH_TOKEN\b")

# `# allow-no-oauth-fallback:` followed by at least one non-space char (a non-empty reason).
_ALLOW_WITH_REASON = re.compile(rf"#\s*{re.escape(_ALLOW)}:\s*\S")

# A top-level `jobs:` key (indent 0), and a job key at the job-indent level (a bare
# `<name>:` — jobs are always block mappings, never inline values).
_JOBS_KEY = re.compile(r"^jobs:\s*(?:#.*)?$")
_JOB_NAME = re.compile(r"^[A-Za-z0-9_-]+:\s*(?:#.*)?$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_structural(line: str) -> bool:
    """A non-blank, non-comment line — the only kind that defines block structure."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _job_ranges(text: str) -> list[tuple[int, int]]:
    """0-based ``[start, end)`` line ranges of each job under top-level ``jobs:``.

    Empty when the text has no top-level ``jobs:`` mapping (e.g. a fragment or a
    composite action), which routes callers to a file-level fallback check.
    Boundaries come from indentation alone: each key at the job-indent level under
    ``jobs:`` opens a job that runs until the next such key or until the block
    dedents below the job indent.
    """
    lines = text.splitlines()
    jobs_idx = next((i for i, ln in enumerate(lines) if _JOBS_KEY.match(ln)), None)
    if jobs_idx is None:
        return []

    # The job-key indent is the indent of the first structural line inside the
    # jobs block. If that line is at indent 0 the block is empty (no jobs).
    job_indent = None
    for ln in lines[jobs_idx + 1 :]:
        if not _is_structural(ln):
            continue
        job_indent = _indent(ln)
        break
    if not job_indent:  # None (nothing follows) or 0 (empty jobs block)
        return []

    starts: list[int] = []
    block_end = len(lines)
    for i in range(jobs_idx + 1, len(lines)):
        ln = lines[i]
        if not _is_structural(ln):
            continue
        indent = _indent(ln)
        if indent < job_indent:  # dedent out of the jobs block ends the last job
            block_end = i
            break
        if indent == job_indent and _JOB_NAME.match(ln.lstrip(" ")):
            starts.append(i)

    return [
        (start, starts[j + 1] if j + 1 < len(starts) else block_end)
        for j, start in enumerate(starts)
    ]


def _fallback_in_scope(
    lineno: int, lines: list[str], ranges: list[tuple[int, int]], text: str
) -> bool:
    """Whether the fallback token is wired in the job enclosing ``lineno`` (1-based).
    A line outside every job falls back to a file-level presence check."""
    idx = lineno - 1
    for start, end in ranges:
        if start <= idx < end:
            return _FALLBACK in "\n".join(lines[start:end])
    return _FALLBACK in text


def find_violations(text: str) -> list[int]:
    """1-based line numbers referencing the primary Claude OAuth token whose enclosing job
    wires no fallback secret and which carry no justified opt-out on/above the line."""
    lines = text.splitlines()
    ranges = _job_ranges(text)
    hits: list[int] = []
    for lineno, raw in enumerate(lines, 1):
        if not _PRIMARY.search(raw):
            continue
        # An opt-out with a non-empty reason on this line or up to two lines above it.
        window = lines[max(0, lineno - 3) : lineno]
        if any(_ALLOW_WITH_REASON.search(prev) for prev in window):
            continue
        if not _fallback_in_scope(lineno, lines, ranges, text):
            hits.append(lineno)
    return hits


if __name__ == "__main__":
    raise SystemExit(
        run_line_checks(
            sys.argv[1:],
            find_violations,
            "uses secrets.CLAUDE_CODE_OAUTH_TOKEN without wiring "
            "secrets.CLAUDE_CODE_OAUTH_TOKEN_FALLBACK in the same job — add the fallback "
            "retry or annotate `# allow-no-oauth-fallback: <reason>`.",
        )
    )
