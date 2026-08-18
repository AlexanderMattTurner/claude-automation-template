#!/usr/bin/env python3
"""Ban an environment-sourced variable inside bash `$(( ))` arithmetic.

An env var read directly inside `$(( ))` (`$((SECONDS + ${TIMEOUT:-90}))`)
trusts its value to be an integer. It routinely is not: a typo or an empty
export makes the expansion an arithmetic SYNTAX ERROR that aborts a `set -e`
caller mid-run, and some garbage values coerce to 0, silently disabling the
limit the arithmetic implements. Remedy: bind the value through a validated
variable FIRST (`[[ "$v" =~ ^[0-9]+$ ]] || v=<default>`), then use that
variable in the arithmetic.

ENVIRONMENT-SOURCED here means an ALL-CAPS name (this repo\'s convention for an
externally-set variable) that the script itself does not ASSIGN on an earlier
line. A name the script assigns first — a counter, a `read` target, a loop
variable — holds whatever that assignment put there, so it carries none of the
"might not be an integer" risk this lint exists for.

Per-line opt-out: a trailing `# env-arith-ok: <reason>` (the reason is
required).

Known blind spots: the scan is per physical line, so a `$(( ))` expression
spanning several lines is not seen, and "assigned earlier" is textual, so a
function that reads a variable assigned below its definition still flags.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import (  # noqa: E402  # pylint: disable=wrong-import-position
    run_line_checks,
    strip_comment,
)

# `$(( ... ))`, one physical line, allowing one level of nested parens.
_ARITH_RE = re.compile(r"\$\(\((?:[^()]|\([^()]*\))*\)\)")
# An ALL-CAPS token of at least two characters: bare (arithmetic context reads
# a name directly), `$NAME`, or `${NAME}`.
_VAR_RE = re.compile(r"\$?\{?\b(?P<name>[A-Z][A-Z0-9_]{1,})\b\}?")
_MARKER_RE = re.compile(r"#\s*env-arith-ok:\s*\S")

# Bash\'s own builtins, always an integer by construction — never a caller\'s env.
_BUILTINS = frozenset({"SECONDS", "RANDOM", "LINENO", "BASHPID", "PPID", "UID", "EUID"})

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_DECLARATORS = r"(?:(?:export|declare|local|readonly|typeset)\s+(?:-\w+\s+)*)?"
# `NAME=`, `NAME+=`, `NAME[i]=`, with an optional declarator and after a
# separator — so `IFS=. cmd` and `x; NAME=1` both count as assignments.
_ASSIGN_RE = re.compile(
    rf"(?:^|[;&|(]|\s){_DECLARATORS}(?P<name>{_NAME})(?:\[[^]]*\])?\+?="
)
_FOR_RE = re.compile(rf"\bfor\s+(?P<name>{_NAME})\s+in\b")
_PRINTF_V_RE = re.compile(rf"\bprintf\b[^;&|]*?-v\s+(?P<name>{_NAME})")
# `read`/`mapfile`/`readarray` bind every plain word after their options.
_READS_RE = re.compile(r"\b(?:read|mapfile|readarray)\b(?P<rest>[^<>|;&]*)")
_READ_VALUE_OPTS = frozenset({"-d", "-n", "-N", "-t", "-u", "-p", "-i", "-c", "-C"})


def _read_targets(rest: str) -> list[str]:
    """The variable names a `read`/`mapfile` argument list binds."""
    names: list[str] = []
    skip = False
    for word in rest.split():
        if skip:
            skip = False
            continue
        if word.startswith("-"):
            skip = word in _READ_VALUE_OPTS
            continue
        if re.fullmatch(_NAME, word):
            names.append(word)
    return names


def assigned_lines(text: str) -> dict[str, int]:
    """{name: first 1-based line the script binds it on}, over every binding
    form this lint reads: an assignment, a `for` variable, a `read`/`mapfile`
    target, and `printf -v`."""
    first: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        code = strip_comment(raw)
        names = [m.group("name") for m in _ASSIGN_RE.finditer(code)]
        names += [m.group("name") for m in _FOR_RE.finditer(code)]
        names += [m.group("name") for m in _PRINTF_V_RE.finditer(code)]
        for match in _READS_RE.finditer(code):
            names += _read_targets(match.group("rest"))
        for name in names:
            first.setdefault(name, lineno)
    return first


def violations(text: str) -> list[int]:
    """1-based line numbers where an ALL-CAPS name the script never assigns
    sits inside `$(( ))`."""
    assigned = assigned_lines(text)
    hits: list[int] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if _MARKER_RE.search(raw):
            continue
        code = strip_comment(raw)
        for span in _ARITH_RE.finditer(code):
            external = {
                name
                for name in _VAR_RE.findall(span.group())
                if name not in _BUILTINS and assigned.get(name, lineno) >= lineno
            }
            if external:
                hits.append(lineno)
                break
    return hits


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "an env-sourced (ALL-CAPS, never assigned here) variable inside "
            "$(( )) — a non-integer value is an arithmetic syntax error that "
            "aborts a set -e caller, and garbage coerced to 0 silently "
            "disables the limit. Validate it into a variable first, or "
            "annotate `# env-arith-ok: <reason>`.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
