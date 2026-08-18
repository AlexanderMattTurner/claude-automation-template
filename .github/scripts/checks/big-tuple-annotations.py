#!/usr/bin/env python3
"""Fail when a type annotation uses a positional `tuple[...]` of >=3 fixed
elements — a "cursed tuple" begging to be a named structure.

A fixed-length heterogeneous tuple (`tuple[str, int, bool]`) forces every call
site to remember what position means what; the fields have no names, and a
reordered pair is a silent bug. Past two elements, convert it to a
`typing.NamedTuple` (a drop-in — it still unpacks, indexes, hashes, and
`== plaintuple`) so the fields carry names.

Flags an annotation subscripting `tuple`/`Tuple` whose slice is a fixed tuple
of THREE OR MORE elements. Variadic `tuple[X, ...]` is never flagged. Exempt a
genuinely-justified case with `# big-tuple-ok: <reason>` on any line the
annotation spans.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

MIN_ELEMENTS = 3
SUPPRESS = "big-tuple-ok:"

TUPLE_NAMES = frozenset({"tuple", "Tuple"})


def _is_tuple_subscript(node: ast.Subscript) -> bool:
    value = node.value
    if isinstance(value, ast.Name):
        return value.id in TUPLE_NAMES
    if isinstance(value, ast.Attribute):
        return value.attr in TUPLE_NAMES
    return False


def _fixed_element_count(node: ast.Subscript) -> int:
    """The count of fixed positional elements, or 0 for a variadic/single-arg
    tuple this check never flags."""
    sl = node.slice
    if not isinstance(sl, ast.Tuple):
        return 0
    elts = sl.elts
    if any(isinstance(e, ast.Constant) and e.value is Ellipsis for e in elts):
        return 0
    return len(elts)


def _suppression_span(node: ast.AST, parents: dict[int, ast.AST]) -> tuple[int, int]:
    """The line range a `big-tuple-ok:` marker may sit in to exempt `node` — the
    enclosing parameter, assignment, or (for a return annotation) the function
    signature, since the formatter may wrap the annotation onto another line."""
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.arg | ast.AnnAssign | ast.Assign):
            return cur.lineno, cur.end_lineno or cur.lineno
        if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef):
            body_start = (
                cur.body[0].lineno if cur.body else (cur.end_lineno or cur.lineno)
            )
            return cur.lineno, body_start - 1
        cur = parents.get(id(cur))
    lineno = getattr(node, "lineno", 1)
    return lineno, getattr(node, "end_lineno", None) or lineno


def _suppressed(node: ast.AST, parents: dict[int, ast.AST], lines: list[str]) -> bool:
    start, end = _suppression_span(node, parents)
    return any(
        SUPPRESS in lines[i - 1] for i in range(start, end + 1) if 0 < i <= len(lines)
    )


def violations(text: str) -> list[int]:
    """1-based lines of every unexempted big positional tuple annotation."""
    tree = ast.parse(text)
    parents = {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    lines = text.splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript) or not _is_tuple_subscript(node):
            continue
        if _fixed_element_count(node) < MIN_ELEMENTS:
            continue
        if not _suppressed(node, parents, lines):
            hits.append(node.lineno)
    return sorted(hits)


def main(argv: list[str]) -> int:
    return run_line_checks(
        argv,
        violations,
        "positional tuple[...] of >=3 elements — convert to a typing.NamedTuple "
        "so the fields have names (or exempt with '# big-tuple-ok: <reason>').",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
