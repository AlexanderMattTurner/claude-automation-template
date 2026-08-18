#!/usr/bin/env python3
"""Flag a call in `tests/` that passes a keyword argument its helper does not accept.

Nothing type-checks a test's call sites, so a rename can land as a semantic
conflict git cannot see: one branch changes a helper's keyword-only
parameter, another adds call sites using the old spelling, both merge clean,
and the suite goes red with `TypeError: <helper>() got an unexpected keyword
argument`.

An AST, whole-tree lint: the definition and the call routinely sit in
different modules, so a per-file check could never resolve the callee. A hit
names a definite `def`:
  * Only a call whose callee is a plain `Name` — never `obj.method(...)`.
  * The name resolves through the calling module's own top-level `def`s
    first, then through `from tests.<module> import <name>` (with its `as`
    alias). Nothing else is followed.
  * A name also bound as a variable or parameter anywhere in the calling
    module is skipped — at the call site it may be a different object.
  * A callee whose signature carries `**kwargs` accepts any name; a call that
    expands `**mapping` is skipped too.

Exempt with `# allow-helper-kwargs: <reason>` on any line the call spans.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(out)


ALLOW = "allow-helper-kwargs"
_ALLOW_RE = re.compile(rf"#\s*{ALLOW}:\s*\S")

FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


class Finding(NamedTuple):
    path: str
    line: int
    callee: str
    problem: str


class Sig(NamedTuple):
    """What one `def` accepts. `keywords` is None when `**kwargs` swallows any
    name; `capacity` is None when `*args` swallows any count. `arity_known` is
    False for a decorated `def`, whose decorator may change the signature."""

    keywords: set[str] | None
    capacity: int | None
    required: set[str]
    positional: list[str]
    arity_known: bool


class CallSite(NamedTuple):
    line: int
    callee: str
    named: frozenset[str]
    n_pos: int
    star_args: bool
    star_kwargs: bool


class Alias(NamedTuple):
    module: str
    bound: str
    original: str


class Facts(NamedTuple):
    rel: str
    key: str
    own: dict[str, Sig]
    imports: tuple[Alias, ...]
    shadowed: frozenset[str]
    calls: tuple[CallSite, ...]


def _signature(fn: FuncDef) -> Sig:
    a = fn.args
    by_position = [*a.posonlyargs, *a.args]
    n_optional = len(a.defaults)
    required = {p.arg for p in by_position[: len(by_position) - n_optional]}
    required |= {
        p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is None
    }
    return Sig(
        keywords=None if a.kwarg else {p.arg for p in (*a.args, *a.kwonlyargs)},
        capacity=None if a.vararg else len(by_position),
        required=required,
        positional=[p.arg for p in by_position],
        arity_known=not fn.decorator_list,
    )


def _module_key(path: Path, root: Path) -> str:
    """`tests/_helpers.py` -> `tests._helpers`, the spelling its importers use."""
    parts = path.relative_to(root).with_suffix("").parts
    return ".".join(("tests", *(p for p in parts if p != "__init__")))


def _is_fixture(fn: FuncDef) -> bool:
    """True for a `@pytest.fixture` (bare or called): its NAME at a call site
    is its yielded value, never the decorated function."""
    for dec in fn.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
        if name == "fixture":
            return True
    return False


def _top_level_defs(tree: ast.Module) -> dict[str, Sig]:
    """A name defined TWICE at module level is dropped rather than resolved to
    either one — which binding a call reaches depends on execution order."""
    defs: dict[str, Sig] = {}
    duplicated: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if _is_fixture(node):
            continue
        if node.name in defs:
            duplicated.add(node.name)
        defs[node.name] = _signature(node)
    return {name: sig for name, sig in defs.items() if name not in duplicated}


def _resolved(facts: Facts, defs: dict[str, dict[str, Sig]]) -> dict[str, Sig]:
    """The helpers this module can call by bare name. Own `def`s win over an
    import of the same name, matching Python's own binding order."""
    out: dict[str, Sig] = {}
    for alias in facts.imports:
        source = defs.get(alias.module)
        if source is not None and alias.original in source:
            out[alias.bound] = source[alias.original]
    out.update(facts.own)
    return out


def _mismatches(call: CallSite, sig: Sig) -> list[str]:
    problems: list[str] = []

    if sig.keywords is not None:
        problems += [
            f"has no parameter `{n}`" for n in sorted(call.named - sig.keywords)
        ]

    if not sig.arity_known or call.star_args:
        return problems

    if sig.capacity is not None and call.n_pos > sig.capacity:
        problems.append(
            f"takes {sig.capacity} positional argument(s), called with {call.n_pos}"
        )
    if not call.star_kwargs:
        supplied = call.named | set(sig.positional[: call.n_pos])
        problems += [f"needs `{n}`" for n in sorted(sig.required - supplied)]
    return problems


def _suppressed(node: ast.Call, lines: list[str]) -> bool:
    start = node.lineno
    end = getattr(node, "end_lineno", None) or start
    return any(
        _ALLOW_RE.search(lines[i - 1])
        for i in range(start, end + 1)
        if 0 < i <= len(lines)
    )


def _module_facts(path: Path, root: Path) -> Facts | None:
    """Everything the whole-tree join needs from one module, in a single walk.
    None when the file is not readable as UTF-8."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    tree = ast.parse(text)

    own = _top_level_defs(tree)
    lines = text.splitlines() if ALLOW in text else []
    imports: list[Alias] = []
    bound: set[str] = set()
    calls: list[CallSite] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                bound.add(node.id)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or _suppressed(node, lines):
                continue
            calls.append(
                CallSite(
                    line=node.lineno,
                    callee=node.func.id,
                    named=frozenset(
                        kw.arg for kw in node.keywords if kw.arg is not None
                    ),
                    n_pos=len(node.args),
                    star_args=any(isinstance(a, ast.Starred) for a in node.args),
                    star_kwargs=any(kw.arg is None for kw in node.keywords),
                )
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            a = node.args
            bound.update(x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs))
            bound.update(x.arg for x in (a.vararg, a.kwarg) if x is not None)
            if not isinstance(node, ast.Lambda):
                bound.add(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.extend(
                Alias(
                    module=node.module,
                    bound=alias.asname or alias.name,
                    original=alias.name,
                )
                for alias in node.names
            )

    return Facts(
        rel=str(path.relative_to(root.parent)),
        key=_module_key(path, root),
        own=own,
        imports=tuple(imports),
        shadowed=frozenset(bound) - set(own),
        calls=tuple(calls),
    )


def findings(root: Path) -> list[Finding]:
    modules = [
        facts
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
        and (facts := _module_facts(path, root)) is not None
    ]
    defs = {m.key: m.own for m in modules}
    hits: list[Finding] = []
    for module in modules:
        resolved = _resolved(module, defs)
        for call in module.calls:
            if call.callee in module.shadowed or call.callee not in resolved:
                continue
            hits.extend(
                Finding(module.rel, call.line, call.callee, problem)
                for problem in _mismatches(call, resolved[call.callee])
            )
    return sorted(hits)


def main() -> int:
    tests_dir = _repo_root() / "tests"
    if not tests_dir.is_dir():
        print(f"cannot check: {tests_dir} is not a directory", file=sys.stderr)
        return 1
    hits = findings(tests_dir)
    if not hits:
        return 0
    print(
        "a test calls a helper in a way its `def` does not accept — that is a "
        "TypeError at run time, not a warning:",
        file=sys.stderr,
    )
    for hit in hits:
        print(f"  {hit.path}:{hit.line}: {hit.callee}() {hit.problem}", file=sys.stderr)
    print(
        "\nremedy: match the signature the helper declares, or annotate a "
        f"deliberate call `# {ALLOW}: <reason>`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
