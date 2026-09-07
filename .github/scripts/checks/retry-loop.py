#!/usr/bin/env python3
"""Ban a new hand-rolled attempt-and-sleep loop in this repo's shell.

`.github/scripts/lib-ci-retry.sh` holds this tree's one retry primitive
(`retry`/`retry_stdout`). A call site that writes the loop again re-decides
the same things — how many attempts, how long to wait, what to print on
exhaustion — and gets one of them wrong.

The defect is a loop with an ATTEMPT BUDGET that sleeps between attempts: it
compares a counter to a literal bound (`for i in 1 2 3`, `while (( i < N ))`,
`while [ "$i" -lt N ]`), and its own body runs `sleep`. A loop bounded by a
wall clock (`SECONDS`, `EPOCHSECONDS`, `EPOCHREALTIME`) is a deadline wait,
not a retry, and is not flagged — it keeps waiting however many attempts a
slow machine needs. Neither is a loop with no counter comparison at all
(`while true`, a poll for a stop condition).

A loop that genuinely must stay hand-rolled opts out with a
`# retry-loop-ok: <reason>` on its own `for`/`while`/`until` line, or in the
comment block directly above it. The reason is required.

Known gap: "counter-bound" is read off the loop's own header, not off
dataflow through the body, and nesting is not excluded, so an inner loop's
sleep can be attributed to its counter-bound outer loop.
"""

import re
import sys
from pathlib import Path

from tree_sitter import Node

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _bash_ast import (  # noqa: E402  # pylint: disable=wrong-import-position
    parse,
    suppressed_lines,
    walk,
)
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ALLOW = "retry-loop-ok:"

_CLOCKS = frozenset({"SECONDS", "EPOCHSECONDS", "EPOCHREALTIME"})

_LOOP_TYPES = ("for_statement", "c_style_for_statement", "while_statement")


def _condition_text(node: Node) -> str:
    """The loop header's condition/list text — the part that decides bound-ness."""
    body = node.child_by_field_name("body")
    header_end = body.start_byte if body is not None else node.end_byte
    return node.text[: header_end - node.start_byte].decode()


def _counter_bound(node: Node) -> bool:
    """True when the loop's own header compares a variable to a literal
    number: a `for … in <numbers>` list, or a `while`/`until` condition
    containing a `-lt`/`-le`/`<`/`<=` (or their negations) against a digit."""
    header = _condition_text(node)
    if node.type == "for_statement":
        return bool(re.search(r"\bin\b.*\d", header))
    return bool(
        re.search(r"(?:-lt|-le|-gt|-ge|<=?|>=?)\s*\$?\{?\w*\}?\s*\d", header)
    ) or bool(re.search(r"\d\s*(?:-lt|-le|-gt|-ge|<=?|>=?)", header))


def _compares_clock(node: Node) -> bool:
    return any(clock in _condition_text(node) for clock in _CLOCKS)


def _own_sleep_line(node: Node) -> int | None:
    """The line of a `sleep` command that is a DIRECT command in this loop's
    body block, or None. Descending into a nested loop's own body is skipped,
    so its sleep is attributed to that inner loop, not this one."""
    body = node.child_by_field_name("body")
    if body is None:
        return None
    stack = list(body.children)
    while stack:
        child = stack.pop()
        if child.type in _LOOP_TYPES:
            continue
        if child.type == "command":
            name_node = child.child_by_field_name("name")
            words = name_node.children if name_node else []
            if words and words[0].type == "word" and words[0].text == b"sleep":
                return child.start_point[0] + 1
            continue
        stack.extend(child.children)
    return None


def violations(text: str) -> list[int]:
    """1-based line numbers of counted attempt-and-sleep loops in TEXT."""
    root = parse(text)
    exempt = suppressed_lines(root, _ALLOW)
    hits: list[int] = []
    for node in walk(root):
        if node.type not in _LOOP_TYPES:
            continue
        if not _counter_bound(node) or _compares_clock(node):
            continue
        sleep_line = _own_sleep_line(node)
        if sleep_line is None:
            continue
        line = node.start_point[0] + 1
        if line not in exempt:
            hits.append(line)
    return sorted(hits)


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "hand-rolled retry: a counted loop that sleeps between attempts. "
            "Written again here, its attempt count and give-up message drift "
            "from lib-ci-retry.sh's `retry`/`retry_stdout`. Call the primitive, "
            "or annotate `# retry-loop-ok: <reason>` on the loop's own line.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
