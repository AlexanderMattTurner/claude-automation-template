#!/usr/bin/env python3
"""Flag a module-level name ASSIGNED MORE THAN ONCE at the top level of one file.

A constant defined twice at module scope silently SHADOWS its first copy — the
second binding wins, and an edit to the first is discarded with no error. An
accidental copy-paste shadow at module scope is almost never intentional.

Considers only statements DIRECTLY in the module body — never inside a
function, class, `if`/`try`/`with`/`for`/`while`, where a re-bind on a branch
is a deliberate alternative. A binding is an `ast.Assign` or a value-carrying
`ast.AnnAssign`; `ast.AugAssign` reads-then-writes and never shadows. A
re-binding whose value reads the name it binds (`x = x + 1`) is an
accumulation, never flagged.

Reported line numbers are the SECOND and each later binding. Exempt with
`# allow-duplicate-constant: <reason>` on any line the statement spans.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ALLOW = "allow-duplicate-constant"


def _target_names(target: ast.expr) -> list[str]:
    """The plain `Name` ids a single target binds, recursing through
    tuple/list unpacking and `Starred`."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _bound_names(stmt: ast.stmt) -> list[str]:
    if isinstance(stmt, ast.Assign):
        names: list[str] = []
        for target in stmt.targets:
            names.extend(_target_names(target))
        return names
    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        return _target_names(stmt.target)
    return []


def _value_reads_name(stmt: ast.stmt, name: str) -> bool:
    """Whether the statement's value expression references `name` — an
    accumulation/rebuild that reads the prior binding, so it is intentional."""
    value = getattr(stmt, "value", None)
    if value is None:
        return False
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(value))


def _suppressed(stmt: ast.stmt, lines: list[str]) -> bool:
    start = stmt.lineno
    end = getattr(stmt, "end_lineno", None) or start
    return any(
        _ALLOW in lines[i - 1] for i in range(start, end + 1) if 0 < i <= len(lines)
    )


def violations(text: str) -> list[int]:
    """1-based line numbers of module-level re-bindings that shadow an earlier
    binding of the same name (the second and each later one)."""
    tree = ast.parse(text)
    lines = text.splitlines()
    seen: set[str] = set()
    hits: set[int] = set()
    for stmt in tree.body:
        for name in _bound_names(stmt):
            if name not in seen:
                seen.add(name)
                continue
            if _value_reads_name(stmt, name) or _suppressed(stmt, lines):
                continue
            hits.add(stmt.lineno)
    return sorted(hits)


def main(argv: list[str]) -> int:
    return run_line_checks(
        argv,
        violations,
        "module-level name re-assigned at top level — the second binding "
        "silently SHADOWS the first. Delete the duplicate, rename it, or "
        "annotate `# allow-duplicate-constant: <reason>`.",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
