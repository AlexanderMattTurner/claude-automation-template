#!/usr/bin/env python3
"""Flag a module that mutates module-level state at runtime and declares no reset.

A module-level binding written from inside a function outlives the call that
wrote it. `pytest-xdist` runs many tests in one long-lived worker that imports
the module once, so one test's write is the next test's answer.

A VIOLATION is a module-level `NAME = ...` binding where BOTH hold: some
function body writes NAME (`global NAME`, `NAME[k] = v`, `del NAME[k]`,
`NAME.attr = v`, `NAME += v`, or a call to a mutator method on it), and the
module defines no top-level `def _reset_process_state`.

Where the AST cannot decide, this prefers a false negative: a name shadowed
inside the writing function, a write through an alias, or a name only ever
written by an importer is never flagged. Exempt with
`# allow-unreset-state: <reason>` on the binding's line.
"""

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ANNOTATION_RE = re.compile(r"#\s*allow-unreset-state:\s*\S")

RESET_NAME = "_reset_process_state"

_MUTATORS = frozenset(
    {
        "add",
        "append",
        "appendleft",
        "clear",
        "difference_update",
        "discard",
        "extend",
        "extendleft",
        "insert",
        "intersection_update",
        "move_to_end",
        "pop",
        "popitem",
        "popleft",
        "remove",
        "rotate",
        "setdefault",
        "sort",
        "symmetric_difference_update",
        "update",
    }
)

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _binding_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store | ast.Del):
            names.add(child.id)
    return names


def module_bindings(tree: ast.Module) -> dict[str, int]:
    """Each module-level name assigned a value, mapped to its first line —
    descends through module-level `if`/`try`/`for`/`with`, never into a
    function or class body."""
    bindings: dict[str, int] = {}

    def visit(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, _SCOPES):
                continue
            if isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = (
                    stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        bindings.setdefault(target.id, stmt.lineno)
                continue
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list):
                    visit([s for s in nested if isinstance(s, ast.stmt)])
            for handler in getattr(stmt, "handlers", []):
                visit(handler.body)

    visit(tree.body)
    return bindings


def _local_names(func: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    """Names `func` binds itself, so a write to one never reaches module scope —
    except a name it declares `global`, whose write IS the module's state."""
    local = {arg.arg for arg in ast.walk(func) if isinstance(arg, ast.arg)}
    declared_global: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Global):
            declared_global.update(node.names)
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            local |= _binding_names(node)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            local |= _binding_names(node.target)
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                if item.optional_vars is not None:
                    local |= _binding_names(item.optional_vars)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            local |= {
                (alias.asname or alias.name).split(".")[0] for alias in node.names
            }
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node is not func
        ):
            local.add(node.name)
    return local - declared_global


def _written_in(func: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    """Module-scope names `func` writes, excluding what it binds locally."""
    written: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Global):
            written.update(node.names)
        elif isinstance(node, ast.Call):
            callee = node.func
            if (
                isinstance(callee, ast.Attribute)
                and callee.attr in _MUTATORS
                and isinstance(callee.value, ast.Name)
            ):
                written.add(callee.value.id)
        elif isinstance(node, ast.Subscript | ast.Attribute) and isinstance(
            node.ctx, ast.Store | ast.Del
        ):
            if isinstance(node.value, ast.Name):
                written.add(node.value.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            written.add(node.target.id)
    return written - _local_names(func)


def _outermost_functions(
    node: ast.AST,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda]:
    """Every function not nested inside another one — a nested function is left
    to its parent's walk, which subtracts the parent's locals too."""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            found.append(child)
        else:
            found.extend(_outermost_functions(child))
    return found


def runtime_writes(tree: ast.Module) -> set[str]:
    written: set[str] = set()
    for func in _outermost_functions(tree):
        written |= _written_in(func)
    return written


def declares_reset(tree: ast.Module) -> bool:
    return any(
        isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
        and stmt.name == RESET_NAME
        for stmt in tree.body
    )


def violations(text: str) -> list[int]:
    """1-based lines of every unexempted module-level binding written at
    runtime by a module that declares no reset."""
    tree = ast.parse(text)
    if declares_reset(tree):
        return []
    lines = text.splitlines()
    written = runtime_writes(tree)
    return sorted(
        lineno
        for name, lineno in module_bindings(tree).items()
        if name in written and not _ANNOTATION_RE.search(lines[lineno - 1])
    )


def main(argv: list[str]) -> int:
    return run_line_checks(
        argv,
        violations,
        "module-level state written at runtime, in a module that declares no "
        f"{RESET_NAME}() — nothing can drop it between test runs that must not "
        "see each other. Declare the reset, replace the state with an "
        "lru_cache-wrapped function, or annotate `# allow-unreset-state: <reason>`.",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
