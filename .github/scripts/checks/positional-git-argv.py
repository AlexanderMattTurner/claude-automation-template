#!/usr/bin/env python3
"""Ban tests that model git's argv POSITIONALLY.

A test that stubs or asserts on a `git` command line by fixed argument
position (`"$1" == "fetch"`, `case "$1" in rev-parse)`) breaks the moment a
real caller inserts a variable number of global options before the
subcommand — `git -c protocol.version=2 fetch` shifts `fetch` to `$3`, so an
anchored assertion goes red (visible) and a `"$1"`-keyed stub goes SILENT: it
stops intercepting and the test passes while asserting nothing.

Fix: locate the subcommand instead of indexing to it — search the recorded
argv for the subcommand token, or key a stub on `case "$1" in -*) ... ;; esac`
that skips leading options before matching.

Opt a call site out with a same-line or preceding-line
`# allow-positional-git-argv: <reason>` when the git call does not route
through a wrapper that can prepend global options.

Known gaps: the stub half only flags subcommand names unique to git
(`rev-parse`, `ls-remote`, …), so a stub keyed on a name other CLIs share
(`fetch`, `clone`, `log`) passes; and comments are told apart from code with
the stdlib `tokenize` module, which does not distinguish string content from
code.
"""

import io
import re
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ALLOW = "allow-positional-git-argv"
# A blank reason does NOT exempt: indistinguishable from a forgotten call site.
_ALLOW_RE = re.compile(rf"{_ALLOW}:\s*\S")

_GIT_ONLY_SUBCOMMANDS = (
    "rev-parse",
    "rev-list",
    "ls-remote",
    "ls-files",
    "ls-tree",
    "for-each-ref",
    "symbolic-ref",
    "show-ref",
    "update-ref",
    "merge-base",
    "hash-object",
    "write-tree",
    "commit-tree",
    "cat-file",
    "check-ignore",
    "diff-index",
    "diff-tree",
    "diff-files",
    "format-patch",
    "range-diff",
    "whatchanged",
    "cherry-pick",
    "reflog",
    "worktree",
)
_SUBCOMMAND_ALT = "|".join(_GIT_ONLY_SUBCOMMANDS)

# `ln.startswith("git fetch …")` / `line == "git rev-parse …"`. `(?!-)` keeps
# `startswith("git --no-pager")`-style global-option checks out of scope.
_ANCHORED_COMMAND_LINE = re.compile(
    r"""(?:\.startswith\(|[=!]=\s*)\s*(?:[a-zA-Z]{0,2})?(?:['"])git\s+(?!-)"""
)

# `[ "$1" = ls-remote ]`, `[[ "$2" == "rev-parse" ]]`, `case "$1" in rev-parse)`.
_POSITIONAL_TEST = re.compile(
    rf""""\$[1-9]"\s*(?:==?|in)\s*['"]?(?:{_SUBCOMMAND_ALT})\b"""
)

_CASE_ON_POSITIONAL = re.compile(r"""case\s+"\$[1-9]"\s+in""")
_CASE_END = re.compile(r"\besac\b")
_CASE_ARM = re.compile(rf"""^\s*(?:{_SUBCOMMAND_ALT})(?:\s*\|[\w|\s-]*)?\)""")


def _comment_lines(text: str) -> set[int]:
    """1-based line numbers that carry a python `#` comment, per the stdlib
    tokenizer — so a match inside a triple-quoted docstring never counts."""
    lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            lines.add(tok.start[0])
    return lines


def violations(text: str) -> list[int]:
    """1-based line numbers that index into git's argv positionally, unannotated."""
    comment_lines = _comment_lines(text)
    lines = text.splitlines()
    hits: list[int] = []
    in_case = False
    for lineno, line in enumerate(lines, 1):
        if lineno in comment_lines:
            continue  # a mention in a comment or docstring header is prose
        if _CASE_ON_POSITIONAL.search(line):
            in_case = True
        if _CASE_END.search(line):
            in_case = False
        if any(
            n in comment_lines and _ALLOW_RE.search(lines[n - 1])
            for n in (lineno, lineno - 1)
            if 1 <= n <= len(lines)
        ):
            continue
        if _ANCHORED_COMMAND_LINE.search(line) or _POSITIONAL_TEST.search(line):
            hits.append(lineno)
            continue
        if in_case and _CASE_ARM.match(line.lstrip()):
            hits.append(lineno)
    return hits


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "this models git's argv positionally, but a wrapper can insert a "
            "variable number of global options before the subcommand — an "
            "assertion anchored this way goes red and a `$1`-keyed git stub "
            "goes SILENTLY vacuous. Locate the subcommand instead, or annotate "
            f"`# {_ALLOW}: <reason>`.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
