#!/usr/bin/env python3
"""Flag a shell function defined in production but called by nothing outside
tests/. A whole-tree token scan: no argv, self-determines the files it reads.

PROBLEM CLASS — a function no production code calls rots: its assumptions
drift from the live code, and it tells a reader a code path exists.

A PRODUCTION shell file is a `.bash`/`.sh` file, or an extensionless file with
a sh/bash shebang, that is not a test file. A function is DEFINED by
`name() {`/`function name {}`, and REFERENCED when its name is a word-boundary
token anywhere outside its own definition line(s). Reference scope is every
non-test, non-doc file, so a shell-out helper or a workflow `run:` block
counts as a caller, but a test's own reference does not — a test file is
excluded from the scan entirely. A single pass does not chase transitivity —
a function called only by another dead function reads as referenced.

Simplified from the source check this was ported from: comments are not
stripped before the token scan (that version used the bash grammar for it),
so a function name mentioned only in a comment reads as referenced — a
deliberate false negative, not a false positive. This version also carries no
grandfathered baseline: any dead function fails the run.
"""

import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ratchet import (  # noqa: E402  # pylint: disable=wrong-import-position
    REPO_ROOT,
    tracked_like_files,
)

_SHELL_SUFFIXES = frozenset({".sh", ".bash"})
_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt"})

ALWAYS_LIVE = frozenset(
    {
        "main",  # conventional top-level entrypoint, invoked as `main "$@"`
        "command_not_found_handle",  # bash's special not-found dispatch hook
        "command_not_found_handler",  # zsh's special not-found dispatch hook
    }
)

_DEF_NAME = r"[A-Za-z_][A-Za-z0-9_-]*"
_FUNCTION_KW_RE = re.compile(
    r"^[ \t]*function[ \t]+(?P<name>" + _DEF_NAME + r")[ \t]*(?:\(\))?[ \t]*\{"
)
_PAREN_RE = re.compile(
    r"^[ \t]*(?P<name>" + _DEF_NAME + r")[ \t]*\(\)[ \t]*(?P<rest>.*)$"
)
_TOKEN_RE = re.compile(r"[\w.:-]+")


def _is_test(rel: str) -> bool:
    parts = Path(rel).parts
    name = Path(rel).name
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.sh")


def _is_shell(path: Path) -> bool:
    if path.suffix in _SHELL_SUFFIXES:
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as f:
            head = f.read(64)
    except OSError:
        return False
    return head.startswith(b"#!") and b"sh" in head.split(b"\n", 1)[0]


def _is_doc(path: Path) -> bool:
    return path.suffix in _DOC_SUFFIXES or "docs" in path.parts


def extract_defs(lines: list[str]) -> list[tuple[str, int]]:
    """(name, 1-based lineno) for every function definition, both `name() {`
    and `function name [()] {` forms."""
    defs: list[tuple[str, int]] = []
    for idx, line in enumerate(lines, start=1):
        kw = _FUNCTION_KW_RE.match(line)
        if kw:
            defs.append((kw.group("name"), idx))
            continue
        m = _PAREN_RE.match(line)
        if m and _paren_has_brace(m.group("rest"), lines, idx):
            defs.append((m.group("name"), idx))
    return defs


def _paren_has_brace(rest: str, lines: list[str], idx: int) -> bool:
    if rest.startswith("{"):
        return True
    if rest:
        return False
    for nxt in lines[idx:]:
        if not nxt.strip():
            continue
        return nxt.lstrip().startswith("{")
    return False


class _ScanFile:
    def __init__(self, rel: str, lines: list[str], is_shell: bool) -> None:
        self.rel = rel
        self.lines = lines
        self.is_shell = is_shell


class DeadFn(NamedTuple):
    rel: str
    name: str
    lineno: int


def find_dead(scan_files: list[_ScanFile]) -> list[DeadFn]:
    """Every function DEFINED in a production shell file with no reference
    outside its own definition line(s), excluding the always-live allowlist."""
    defs_by_file: dict[str, list[tuple[str, int]]] = {}
    own_tokens: Counter[str] = Counter()
    for sf in scan_files:
        if not sf.is_shell:
            continue
        file_defs = extract_defs(sf.lines)
        defs_by_file[sf.rel] = file_defs
        for name, lineno in file_defs:
            own_tokens[name] += _TOKEN_RE.findall(sf.lines[lineno - 1]).count(name)

    total: Counter[str] = Counter()
    for sf in scan_files:
        for line in sf.lines:
            total.update(_TOKEN_RE.findall(line))

    dead: list[DeadFn] = []
    for rel, file_defs in defs_by_file.items():
        for name, lineno in file_defs:
            if name in ALWAYS_LIVE:
                continue
            if total[name] - own_tokens[name] > 0:
                continue
            dead.append(DeadFn(rel, name, lineno))
    return sorted(dead)


def _load_scan_files(root: Path = REPO_ROOT) -> list[_ScanFile]:
    """Every non-test, non-doc file — a test reference is excluded outright,
    not merely spared from defining, so a function called only from tests/
    scores zero production references rather than being masked as live."""
    scan: list[_ScanFile] = []
    for rel in tracked_like_files(root):
        path = root / rel
        if _is_doc(path) or _is_test(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scan.append(_ScanFile(rel, text.splitlines(), is_shell=_is_shell(path)))
    return scan


def main(root: Path = REPO_ROOT) -> None:
    dead = find_dead(_load_scan_files(root))
    if not dead:
        return
    problems = [
        f"{rel}::{name}: function defined at line {lineno} is referenced only "
        "from tests/ (or nowhere) — no production shell code calls it. Remove it."
        for rel, name, lineno in dead
    ]
    print(
        "dead-shell-function violations:\n  " + "\n  ".join(problems), file=sys.stderr
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
