#!/usr/bin/env python3
"""Fail when a Python module builds a ``git`` command line that names no
repository — the argv then acts on whatever directory the process is sitting in.

The defect this guards: a helper builds ``subprocess.run(["git", *args])`` and
one caller passes ``["merge", "--abort"]``. A test that drives that helper
IN-PROCESS (no ``cwd=`` set) then aborts the developer's own merge — staged
work gone, HEAD unmoved, nothing printed that reads as damage.

The rule: a git call names its repository, as the argv's FIRST option
(``["git", "-C", repo, ...]``) or as the call's own ``cwd=``, unless every
subcommand it can run is read-only. ``["git", "merge", "--abort"]`` is flagged,
and so is ``["git", *args]``, whose subcommand can be anything.

Opt a site out with ``# cwd-git-ok: <reason>`` on it or the line above.

Simplified from the source check this was ported from: that version ran over
every tracked Python file and enforced a grandfathered baseline ratchet; this
one runs over the files pre-commit passes and fails on any hit (no baseline),
since the target tree has none of its own yet.

Known gap: the argv must be a literal AT the call, so ``cmd = [...]`` then
``subprocess.run(cmd)`` needs dataflow and is not seen.
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

SUPPRESS = "cwd-git-ok:"

# Subcommands that only READ. Anything outside this set — and anything this
# check cannot resolve to a literal — must name its repository. The list is
# deliberately a floor: an unclassified subcommand is treated as able to
# write, so a new git verb fails closed.
READ_ONLY = frozenset(
    {
        "cat-file",
        "check-attr",
        "check-ignore",
        "config",
        "diff",
        "diff-tree",
        "for-each-ref",
        "grep",
        "hash-object",
        "log",
        "ls-files",
        "ls-remote",
        "ls-tree",
        "merge-base",
        "name-rev",
        "rev-list",
        "rev-parse",
        "show",
        "show-ref",
        "status",
        "symbolic-ref",
        "var",
        "version",
    }
)


def _argv_node(node: ast.Call) -> ast.expr | None:
    """The first positional argument of a call, however it was spelled —
    ``subprocess.run(args=["git", …])`` passes the argv as a keyword."""
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg == "args":
            return keyword.value
    return None


def _git_argv(node: ast.Call) -> ast.List | ast.Tuple | None:
    """The argv sequence literal of a subprocess call whose program is ``git``."""
    argv = _argv_node(node)
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return None
    first = argv.elts[0]
    if isinstance(first, ast.Constant) and first.value == "git":
        return argv
    return None


def _names_a_repo(node: ast.Call, argv: ast.List | ast.Tuple) -> bool:
    """Whether the call names the repository it acts on: as the argv's first
    option, or as the subprocess call's own ``cwd=``."""
    if any(keyword.arg == "cwd" for keyword in node.keywords):
        return True
    if len(argv.elts) < 2:
        return False
    second = argv.elts[1]
    return isinstance(second, ast.Constant) and second.value == "-C"


def _creates_its_own_repo(argv: ast.List | ast.Tuple) -> bool:
    """Whether this argv CREATES a repository elsewhere, so naming one is
    unneeded — ``git clone`` always writes into a new directory; ``git init``
    only when it ends in a path the code BUILT (not a trailing literal, which
    reads as e.g. ``git init -b main``)."""
    for element in argv.elts[1:]:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return False
        if element.value.startswith("-"):
            continue
        if element.value == "clone":
            return True
        if element.value == "init":
            return not isinstance(argv.elts[-1], ast.Constant)
        return False
    return False


def _writes(argv: ast.List | ast.Tuple) -> bool:
    """Whether this argv needs a repository, i.e. can run something that writes."""
    for element in argv.elts[1:]:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return True  # a splat or a variable: the subcommand can be anything
        word = element.value
        if word.startswith("-"):
            continue
        return word not in READ_ONLY
    return False


def _suppressed(node: ast.Call, lines: list[str]) -> bool:
    first = max(node.lineno - 2, 0)
    last = getattr(node, "end_lineno", node.lineno)
    return any(SUPPRESS in line for line in lines[first:last])


def violations(text: str) -> list[int]:
    """1-based line numbers of git calls in TEXT that name no repository."""
    tree = ast.parse(text)
    lines = text.splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        argv = _git_argv(node)
        if argv is None or _names_a_repo(node, argv) or _creates_its_own_repo(argv):
            continue
        if not _writes(argv) or _suppressed(node, lines):
            continue
        hits.append(node.lineno)
    return sorted(hits)


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "git argv names no repository — it acts on whatever directory the "
            "process is in, so an in-process run reaches the caller's own "
            "checkout. Put `-C <repo>` first, pass `cwd=`, or annotate "
            "`# cwd-git-ok: <reason>`.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
