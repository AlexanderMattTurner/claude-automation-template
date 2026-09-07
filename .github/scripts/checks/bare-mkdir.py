#!/usr/bin/env python3
"""Flag a bare `mkdir -p` in shell code — its exit status alone proves nothing.

On macOS/BSD, `mkdir -p "$X"` exits 0 even when `$X` is an existing DANGLING
symlink, so trusting its exit status lets a later write into `$X` die
cryptically instead of failing where the real problem is. A caller that needs
`$X` to exist afterward must verify the post-condition (`[[ -d "$X" ]]`), not
just check `mkdir`'s exit code.

A VIOLATION line invokes `mkdir` with a `-p`-carrying flag cluster (`-p`,
`-pm`, `-m 700 -p`, `--parents`). Plain `mkdir` without `-p` is not flagged —
only `-p`'s dangling-symlink lie needs the post-condition check.

Exempt: a line with `# bare-mkdir-ok: <reason>`.

Simplified from the source check this was ported from: comment stripping here
is a single-line heuristic (cut at the first unquoted `#`), not a full bash
grammar, so a `#` inside a quoted string can be mis-read as starting a
comment. Known gap: line-based, so `-p` reaching `mkdir` via a continuation
line or a variable (`flags=-p; mkdir $flags`) is missed.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import (  # noqa: E402  # pylint: disable=wrong-import-position
    run_line_checks,
    strip_comment,
)

# `mkdir` as a standalone command word (not `sbx-mkdir`, `bin/mkdir`, `mkdir2`).
_MKDIR_RE = re.compile(r"(?<![\w./-])mkdir(?![\w.-])")
# A flag cluster carrying `p` (`-p`, `-pm`, `-mp`, `--parents`) as its own word.
_P_FLAG_RE = re.compile(r"(?:^|\s)(?:-[A-Za-z]*p[A-Za-z]*|--parents)(?=\s|$)")
# The rest of a `mkdir` invocation ends at the next command separator.
_SEPARATOR_RE = re.compile(r"[;|&]")
_ANNOTATION_RE = re.compile(r"#\s*bare-mkdir-ok:\s*\S")


def line_has_bare_mkdir_p(stripped: str) -> bool:
    """True when the comment-stripped line invokes `mkdir` with a `-p`-carrying
    flag cluster before the next command separator."""
    for m in _MKDIR_RE.finditer(stripped):
        rest = stripped[m.end() :]
        sep = _SEPARATOR_RE.search(rest)
        if sep:
            rest = rest[: sep.start()]
        if _P_FLAG_RE.search(rest):
            return True
    return False


def violations(text: str) -> list[int]:
    """1-based line numbers of unexempted bare `mkdir -p` invocations."""
    hits = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if _ANNOTATION_RE.search(raw):
            continue
        if line_has_bare_mkdir_p(strip_comment(raw)):
            hits.append(lineno)
    return hits


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "bare `mkdir -p` — on macOS/BSD its exit status is 0 even over a "
            "dangling symlink, so a later write dies cryptically. Verify the "
            'post-condition (`[[ -d "$X" ]]`), or annotate '
            "`# bare-mkdir-ok: <reason>`.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
