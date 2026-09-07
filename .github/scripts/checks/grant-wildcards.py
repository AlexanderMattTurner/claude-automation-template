#!/usr/bin/env python3
"""Ban a `permissions.allow` Bash grant whose `*` extends a word.

A grant is matched by `.claude/hooks/lib-checks.sh`'s pattern matcher, which
turns `*` into a wildcard and anchors the result. `Bash(git diff*)` therefore
auto-approves not just `git diff HEAD` but `git difftool`, which executes an
arbitrary command named in git config — the wildcard silently spanned the
token that SELECTS WHAT RUNS.

The rule, structural and hermetic — no table of real command names to drift:

    In a `Bash(<spec>)` entry of `permissions.allow`, the character
    immediately before any `*` must not be `[A-Za-z0-9]`.

That admits every wildcard beginning at a delimiter — `Bash(git diff *)`,
`Bash(pnpm test:*)` — because a wildcard after a space/`:`/`-` extends the
ARGUMENTS of a command already fully named.

Remedy: the two-form grant — `Bash(git diff)` plus `Bash(git diff *)` — so the
wildcard starts at a delimiter and `git difftool` still prompts.

Scope is `permissions.allow` only; `deny` entries must span everything they
can, the opposite shape. Invoked by pre-commit with the staged settings files.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_MESSAGE = (
    "a Bash allow-grant's `*` extends a word, so it spans every longer command "
    "sharing that prefix (`Bash(git diff*)` auto-approves `git difftool`). Write "
    "the two-form grant instead: `Bash(git diff)` plus `Bash(git diff *)`."
)

# The whole rule: a `*` immediately preceded by a word character. Anything else
# before the `*` is a delimiter, so the wildcard cannot extend the command word.
_WORD_EXTENDING = re.compile(r"[A-Za-z0-9]\*")


def spans_a_word(spec: str) -> bool:
    """True when SPEC (the text inside `Bash(...)`) has a `*` immediately
    following an alphanumeric character — the word-extending wildcard."""
    return bool(_WORD_EXTENDING.search(spec))


def bash_spec(grant: str) -> str | None:
    """The text inside a `Bash(...)` grant, or None for any other tool's grant."""
    if grant.startswith("Bash(") and grant.endswith(")"):
        return grant[len("Bash(") : -1]
    return None


def _line_of(lines: list[str], needle: str, taken: set[int]) -> int:
    """The 1-based line carrying NEEDLE (a JSON-encoded grant), skipping lines
    already reported so a duplicated grant points at both of its entries."""
    for lineno, line in enumerate(lines, 1):
        if needle in line and lineno not in taken:
            return lineno
    return 1


def violations(text: str) -> list[int]:
    """1-based line numbers of `permissions.allow` Bash grants whose wildcard
    extends a word. A malformed file (not valid JSON) is not this lint's
    concern — the JSON validator hook owns that failure."""
    try:
        allow = json.loads(text).get("permissions", {}).get("allow")
    except json.JSONDecodeError:
        return []
    if not isinstance(allow, list):
        return []
    lines = text.splitlines()
    hits: set[int] = set()
    for grant in allow:
        spec = bash_spec(grant) if isinstance(grant, str) else None
        if spec is None or not spans_a_word(spec):
            continue
        hits.add(_line_of(lines, json.dumps(grant), hits))
    return sorted(hits)


def main(argv: list[str]) -> None:
    sys.exit(run_line_checks(argv, violations, _MESSAGE))


if __name__ == "__main__":
    main(sys.argv[1:])
