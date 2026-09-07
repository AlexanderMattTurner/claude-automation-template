"""Shared machinery for the line-oriented pre-commit lints under this directory.

Each line-lint script scans a list of paths given on argv, reads each file as
UTF-8 (skipping anything unreadable), runs a per-script detector over the text,
and prints ``<path>:<lineno>: <message>`` to stderr for every hit — returning 1
if any fired. The read loop, the skip-on-OSError/UnicodeDecodeError, the print
loop, and the exit code live here so each script body is just its detector.

Imported as a sibling: the scripts run as ``python3 .github/scripts/check-*.py``,
so this directory is already ``sys.path[0]``; the tests load each script by path,
so each prepends its own dir to ``sys.path`` before importing this module.
"""

import sys
from collections.abc import Callable


def strip_comment(line: str) -> str:
    """LINE with a trailing ``#...`` comment cut, honoring single/double quotes.

    PROBLEM CLASS — "cut the shell comment off this line before matching code
    on it". Naive: no escape handling, no heredoc awareness, one physical
    line. A lint that needs more than that reads the bash grammar instead
    (`.github/scripts/checks/_bash_ast.py`).
    """
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def run_line_checks(
    argv: list[str],
    find_violations: Callable[[str], list[int]],
    message: str,
) -> int:
    """Drive a line-oriented lint over ARGV.

    For each readable path, FIND_VIOLATIONS(text) returns the 1-based line numbers
    that violate. Each hit prints ``<path>:<lineno>: <message>`` to stderr; an
    unreadable path (OSError / UnicodeDecodeError) is skipped. Returns 1 if any
    path produced a hit, else 0.
    """
    status = 0
    for path in argv:
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno in find_violations(text):
            print(f"{path}:{lineno}: {message}", file=sys.stderr)
            status = 1
    return status
