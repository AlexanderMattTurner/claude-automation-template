#!/usr/bin/env python3
"""Ban a test assertion that compares a wall-clock duration against a number.

A shared CI runner gives a test no promise about when it runs — a spawn, a
page fault, or a descheduled thread routinely costs hundreds of milliseconds
that belong to the machine's load, not the code. An assertion on the
resulting duration measures the runner and flakes, in both directions:
`assert elapsed < N` claims the code is fast (assert the bound firing's
observable instead — an exit status, a marker file); `assert elapsed >= N`
claims the code waited (install a recording `sleep` stub and assert the
seconds it was asked for).

A VIOLATION is an `assert` whose test compares a wall-clock DELTA against a
numeric literal. A delta is a subtraction involving `time.monotonic()`,
`time.time()`, `time.perf_counter()` or their `_ns` forms (or, in a
JavaScript test, `Date.now()`/`performance.now()`), written inline, reached
through a local bound to one earlier in the same function, or returned by a
helper in the same module. NOT flagged: a deadline poll
(`while time.monotonic() < deadline`), and a comparison against a
non-literal.

Exempt with `# allow-wall-clock: <reason>` (`//` in JS) on any line the
assert spans, or the line above it. The JavaScript half is a regex
heuristic, so it can misread a clock mention inside a string.
"""

import ast
import re
import sys
from pathlib import Path

_ANNOTATION_RE = re.compile(r"(?:#|//)\s*allow-wall-clock:\s*\S")

_JS_CLOCK_RE = re.compile(r"\b(?:Date\.now|performance\.now)\s*\(\s*\)")
_JS_ASSERT_LINE_RE = re.compile(r"\bassert[.\w]*\s*\(")
_JS_NUMBER_RE = re.compile(r"[<>=!]=?\s*-?\d|(?<![<>=!])-?\d+(?:\.\d+)?\s*[<>=!]=?")

_CLOCKS = frozenset(
    {
        "monotonic",
        "monotonic_ns",
        "perf_counter",
        "perf_counter_ns",
        "process_time",
        "time",
        "time_ns",
    }
)
_DURATION_PRESERVING = frozenset({"abs", "float", "int", "round"})
_SCALERS = frozenset({"scale_timeout"})

_ReturnMap = dict[str, frozenset[int | None]]


def _is_clock_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _CLOCKS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "time"
    )


def _clock_names(
    scope: ast.AST, inherited: frozenset[str], returns: _ReturnMap
) -> set[str]:
    """Names bound to a clock reading, or a duration derived from one, in
    `scope` — resolved per function, seeded by module-level bindings, so a
    one-letter name reused across tests never cross-contaminates."""
    names = set(inherited)
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        positions = _returned_positions(value, returns)
        if isinstance(target, ast.Name):
            if _mentions_clock(value, names) or None in positions:
                names.add(target.id)
        elif isinstance(target, ast.Tuple):
            for index, element in enumerate(target.elts):
                if isinstance(element, ast.Name) and index in positions:
                    names.add(element.id)
    return names


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _returned_positions(value: ast.expr, returns: _ReturnMap) -> frozenset[int | None]:
    if not isinstance(value, ast.Call):
        return frozenset()
    return returns.get(_callee_name(value.func) or "", frozenset())


def _own_returns(func: ast.AST) -> list[ast.Return]:
    """`func`'s own `return` statements, skipping a nested def's."""
    found: list[ast.Return] = []
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _duration_returns(tree: ast.Module) -> _ReturnMap:
    """{helper name: where its return value is a duration}, grown to a fixed
    point so a helper that returns another helper's duration counts too."""
    inherited = _module_level_names(tree, {})
    candidates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        values = [r.value for r in _own_returns(node) if r.value is not None]
        if values:
            candidates.append((node, values))

    returns: _ReturnMap = {}
    grew = True
    while grew:
        grew = False
        for func, values in candidates:
            names = _clock_names(func, inherited, returns)
            found: set[int | None] = set(returns.get(func.name, frozenset()))
            for value in values:
                if isinstance(value, ast.Tuple):
                    found.update(
                        index
                        for index, element in enumerate(value.elts)
                        if _is_duration(element, names, returns)
                    )
                elif _is_duration(value, names, returns):
                    found.add(None)
            if found != set(returns.get(func.name, frozenset())):
                returns[func.name] = frozenset(found)
                grew = True
    return returns


def _mentions_clock(node: ast.expr, names: set[str]) -> bool:
    for child in ast.walk(node):
        if _is_clock_call(child):
            return True
        if isinstance(child, ast.Name) and child.id in names:
            return True
    return False


def _is_duration(node: ast.expr, names: set[str], returns: _ReturnMap) -> bool:
    """Whether `node` is a wall-clock DURATION rather than a point in time."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Sub) and _mentions_clock(node, names):
            return True
        return _is_duration(node.left, names, returns) or _is_duration(
            node.right, names, returns
        )
    if isinstance(node, ast.Call):
        if None in _returned_positions(node, returns):
            return True
        callee = node.func
        if (
            node.args
            and isinstance(callee, ast.Name)
            and callee.id in _DURATION_PRESERVING
        ):
            return any(_is_duration(arg, names, returns) for arg in node.args)
    return False


def _is_number(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return _is_number(node.operand)
    if isinstance(node, ast.Call) and _callee_name(node.func) in _SCALERS:
        return bool(node.args) and all(map(_is_number, node.args))
    return isinstance(node, ast.Constant) and isinstance(node.value, int | float)


def _offends(node: ast.Assert, names: set[str], returns: _ReturnMap) -> bool:
    for compare in ast.walk(node.test):
        if not isinstance(compare, ast.Compare):
            continue
        sides = [compare.left, *compare.comparators]
        if any(_is_duration(s, names, returns) for s in sides) and any(
            _is_number(s) for s in sides
        ):
            return True
    return False


def _module_level_names(tree: ast.Module, returns: _ReturnMap) -> frozenset[str]:
    module_only = ast.Module(
        body=[n for n in tree.body if isinstance(n, ast.Assign)], type_ignores=[]
    )
    return frozenset(_clock_names(module_only, frozenset(), returns))


def _scopes(
    tree: ast.Module, returns: _ReturnMap
) -> list[tuple[ast.AST, frozenset[str]]]:
    inherited = _module_level_names(tree, returns)
    outside = ast.Module(
        body=[
            n
            for n in tree.body
            if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        ],
        type_ignores=[],
    )
    scopes: list[tuple[ast.AST, frozenset[str]]] = [(outside, inherited)]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scopes.append((node, inherited))
    return scopes


def violations(text: str) -> list[int]:
    """1-based lines of every unexempted wall-clock assertion in one Python module."""
    tree = ast.parse(text)
    lines = text.splitlines()

    def exempt(node: ast.Assert) -> bool:
        first = node.lineno
        last = node.end_lineno or first
        return any(
            _ANNOTATION_RE.search(lines[n - 1])
            for n in range(max(first - 1, 1), last + 1)
        )

    returns = _duration_returns(tree)
    hits: set[int] = set()
    for scope, inherited in _scopes(tree, returns):
        names = _clock_names(scope, inherited, returns)
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.Assert)
                and _offends(node, names, returns)
                and not exempt(node)
            ):
                hits.add(node.lineno)
    return sorted(hits)


def js_violations(text: str) -> list[int]:
    """1-based lines where a clock reading and a numeric comparison sit on the
    same or an adjacent `assert*(` line — the regex approximation of the
    Python check above (see module docstring for why it is not an AST walk)."""
    lines = text.splitlines()
    hits: set[int] = set()
    for n, line in enumerate(lines, start=1):
        if not _JS_ASSERT_LINE_RE.search(line):
            continue
        window = "\n".join(lines[n - 1 : min(n + 2, len(lines))])
        if (
            _JS_CLOCK_RE.search(window)
            and "-" in window
            and _JS_NUMBER_RE.search(window)
        ):
            span = range(max(n - 1, 1), min(n + 2, len(lines)) + 1)
            if not any(_ANNOTATION_RE.search(lines[i - 1]) for i in span):
                hits.add(n)
    return sorted(hits)


def main(argv: list[str]) -> int:
    status = 0
    for path in argv:
        suffix = Path(path).suffix
        if suffix not in {".py", ".mjs"}:
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = js_violations(text) if suffix == ".mjs" else violations(text)
        for lineno in hits:
            print(
                f"{path}:{lineno}: wall-clock assertion — a loaded shared runner "
                "inflates the delta, so this measures the runner. Assert the "
                "PROPERTY the bound firing produces, or annotate "
                "`# allow-wall-clock: <reason>`.",
                file=sys.stderr,
            )
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
