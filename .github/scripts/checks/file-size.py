#!/usr/bin/env python3
"""Report a source file that outgrows the size cap, as a grandfathered ratchet.

Policy and baseline both live in ``config/file-size-baseline.json``: ``cap`` is
the line ceiling every file must reach for eventually, ``baseline`` maps a
currently-over-cap file to its code-line count at baseline time. A file over
cap must match its baseline exactly — growing past it is a new violation
(``run.py`` gained lines), shrinking below it is a stale entry that must be
regenerated (``--write-baseline``) so the list only ever shrinks. A file over
cap with no baseline entry is a brand-new violation.

A "line" is a CODE line: comment-only and blank lines don't count, so prose
never prices against the cap. The comment strip here is line-oriented (a line
whose first non-blank character starts a ``#``/``//`` comment) rather than a
full tokenizer, so it under-strips a same-line trailing comment and a
multi-line string/docstring body — a deliberately conservative simplification
that only ever over-counts, never hides a real violation.

Scope: tracked-like files (``_ratchet.tracked_like_files``: walked from the
repo root, skipping only the VCS/dependency/cache directories it names) with a
source suffix, plus extensionless shebang executables. Generated bundles
(``*.bundle.mjs``) and test files (a ``tests/`` directory, or a
``test_*.py``/``*.test.mjs`` name) are excluded — a test file grows one case at
a time and carries no production-runtime risk a size cap guards against.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ratchet import (  # noqa: E402  # pylint: disable=wrong-import-position
    REPO_ROOT,
    findings as _ratchet_findings,
    load_policy,
    tracked_like_files,
    write_baseline as _ratchet_write_baseline,
)

SOURCE_SUFFIXES = frozenset({".py", ".mjs", ".js", ".cjs", ".bash", ".sh"})
_JS_SUFFIXES = frozenset({".mjs", ".js", ".cjs"})
_LINE_COMMENT = {
    ".py": "#",
    ".bash": "#",
    ".sh": "#",
    ".mjs": "//",
    ".js": "//",
    ".cjs": "//",
}

GROWTH_EXIT_STATUS = 3


def _baseline_path() -> Path:
    return REPO_ROOT / "config" / "file-size-baseline.json"


def _is_test(rel: str) -> bool:
    parts = Path(rel).parts
    name = Path(rel).name
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.mjs")
        or name.endswith(".test.js")
    )


def _is_source(path: Path) -> bool:
    if path.name.endswith(".bundle.mjs"):
        return False  # esbuild output: size is the bundler's, not a reader's
    if path.suffix in SOURCE_SUFFIXES:
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def _code_line_count(path: Path, text: str) -> int:
    """TEXT's number of lines carrying real code, per the line-oriented strip
    described in the module header."""
    marker = _LINE_COMMENT.get(path.suffix)
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if marker and stripped.startswith(marker):
            continue
        count += 1
    return count


def scan_sizes(
    root: Path = REPO_ROOT, files: list[str] | None = None
) -> dict[str, int]:
    """{rel: code_line_count} for every in-scope source file."""
    rels = files if files is not None else tracked_like_files(root)
    sizes = {}
    for rel in rels:
        path = root / rel
        if not path.is_file() or not _is_source(path) or _is_test(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        sizes[rel] = _code_line_count(path, text)
    return sizes


def findings(
    sizes: dict[str, int], policy: dict, *, complete: bool = True
) -> list[str]:
    """Ratchet violations: a new over-cap file, a grown baseline entry, a
    shrunk (stale) baseline entry, or a baseline entry no longer over cap —
    the ``_ratchet`` shape (also a deleted file's entry, in a whole-tree scan)."""
    return _ratchet_findings(
        sizes, policy, "lines", cap_desc=f"{policy['cap']}-line", complete=complete
    )


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--write-baseline":
        policy = load_policy(_baseline_path())
        _ratchet_write_baseline(_baseline_path(), policy, scan_sizes())
        return

    policy = load_policy(_baseline_path())
    sizes = scan_sizes(files=argv or None)
    growth = findings(sizes, policy, complete=not argv)
    if growth:
        print("file-size violations:\n  " + "\n  ".join(growth), file=sys.stderr)
        raise SystemExit(GROWTH_EXIT_STATUS)


if __name__ == "__main__":
    main(sys.argv[1:])
