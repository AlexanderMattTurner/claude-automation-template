#!/usr/bin/env python3
"""Demand that every `source`/`.` statement shellcheck can follow, or says why not.

shellcheck follows a `source` line only when it can tell which file it reads;
a variable-built target needs a `# shellcheck source=<path>` comment on the
line above. Two silent failures are banned: UNRESOLVABLE, a comment naming a
path the tree lacks (masked by a nearby `disable=SC1091`), and UNDECLARED, a
variable target with no directive at all. Either way shellcheck exits 0
having never read the library, so a real bug in it is invisible here.

A LITERAL target (no `$`/backtick) needs no directive — shellcheck resolves it
on its own — so only a target built from an expansion is checked. `/dev/null`
is shellcheck's own do-not-follow marker and is never a violation, and neither
is `# shellcheck disable=SC1090` (a deliberate "cannot be known ahead of
time").

Simplified from the source check this was ported from: target resolution here
is a plain filesystem lookup relative to the sourcing file's own directory,
the repo root, or one of this repo's `-P` search paths
(`.pre-commit-config.yaml`'s `-P ".claude/hooks:.github/scripts"`) — a real
`-P` list edit needs the matching edit here, unlike shellcheck's own `-x`,
which needs none. No brace-expansion or symlink resolution. Blind spot: a
directive inside a heredoc resolves where the GENERATED script lands, not here.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import (  # noqa: E402  # pylint: disable=wrong-import-position
    strip_comment,
)
from _ratchet import REPO_ROOT  # noqa: E402  # pylint: disable=wrong-import-position

_DIRECTIVE_RE = re.compile(r"#\s*shellcheck\s+source=(?P<target>\S+)")
_DISABLE_1090_RE = re.compile(r"#\s*shellcheck\s+disable=(?:[\w,]*\bSC1090\b[\w,]*)")
# `source foo.sh` / `. foo.sh` as the first word of a (comment-stripped) line.
_SOURCE_STMT_RE = re.compile(r"^\s*(?:source|\.)\s+(?P<target>[^\s;|&]+)")


# Extra search roots shellcheck is invoked with (`-P` in .pre-commit-config.yaml),
# tried in addition to the sourcing file's own directory and the repo root.
_SEARCH_ROOTS = (".claude/hooks", ".github/scripts")


def _resolves(rel: str, target: str) -> bool:
    if target == "/dev/null":
        return True
    directory = Path(rel).parent
    candidates = [REPO_ROOT / directory / target, REPO_ROOT / target]
    candidates += [REPO_ROOT / root / target for root in _SEARCH_ROOTS]
    return any(candidate.is_file() for candidate in candidates)


def violations(text: str, rel: str = "<script>") -> list[int]:
    """1-based line numbers of `source`/`.` statements shellcheck cannot
    follow: a directive naming a target this tree lacks, or a variable target
    with no directive and no `disable=SC1090`."""
    lines = text.splitlines()
    hits: list[int] = []
    for lineno, raw in enumerate(lines, start=1):
        code = strip_comment(raw)
        stmt = _SOURCE_STMT_RE.match(code)
        if not stmt:
            continue
        target = stmt.group("target").strip("'\"")
        preceding = lines[lineno - 2] if lineno >= 2 else ""
        directive = _DIRECTIVE_RE.search(preceding)
        if directive:
            if not _resolves(rel, directive.group("target")):
                hits.append(lineno)
            continue
        if "$" in target or "`" in target:
            if not _DISABLE_1090_RE.search(preceding):
                hits.append(lineno)
    return hits


def main(argv: list[str]) -> None:
    status = 0
    for path in argv:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = (
            str(Path(path).resolve().relative_to(REPO_ROOT))
            if Path(path).is_absolute()
            else path
        )
        for lineno in violations(text, rel):
            print(
                f"{path}:{lineno}: `source`/`.` statement shellcheck cannot "
                "follow — give it a `# shellcheck source=<path>` comment naming "
                "a path that resolves, or `# shellcheck disable=SC1090` when the "
                "target genuinely cannot be known ahead of time.",
                file=sys.stderr,
            )
            status = 1
    sys.exit(status)


if __name__ == "__main__":
    main(sys.argv[1:])
