#!/usr/bin/env python3
"""Flag a text-mode filesystem call that does not name its encoding.

`Path.read_text()` / `write_text()` / `open()` with no `encoding=` decode with
the platform default (`locale.getencoding()`), which is not UTF-8 on every
host, so a rule file or fixture holding an em-dash mis-decodes there and
nowhere else. Also flags a `tempfile.{NamedTemporaryFile,TemporaryFile,
SpooledTemporaryFile}` opened in text mode, which defaults to binary.

Where the AST cannot decide, this prefers a false negative: a non-constant
mode, a `*args`/`**kwargs` splat, or `<obj>.open(...)` (shared by `Path.open`,
`tarfile.open`, `gzip.open`, whose signatures differ) are never flagged.
Exempt with `# allow-unspecified-encoding: <reason>` on the call's method-name
line or the line its argument list closes on.
"""

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ratchet import (  # noqa: E402  # pylint: disable=wrong-import-position
    REPO_ROOT,
    findings as _ratchet_findings,
    load_policy,
    tracked_like_files,
    write_baseline,
)

_ANNOTATION_RE = re.compile(r"#\s*allow-unspecified-encoding:\s*\S")

_TEXT_METHODS = {"read_text": 0, "write_text": 1}  # method -> `encoding` arg index

# factory -> (`mode` index, `encoding` index). All default to binary (`w+b`).
_TEMPFILE_FACTORIES = {
    "NamedTemporaryFile": (0, 2),
    "TemporaryFile": (0, 2),
    "SpooledTemporaryFile": (1, 3),
}

_OPEN_MODE_INDEX = 1
_OPEN_ENCODING_INDEX = 3

_TEXT, _BINARY, _UNKNOWN = "text", "binary", "unknown"


def _has_splat(call: ast.Call) -> bool:
    return any(isinstance(a, ast.Starred) for a in call.args) or any(
        kw.arg is None for kw in call.keywords
    )


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _supplies(call: ast.Call, name: str, index: int) -> bool:
    """Whether `call` passes a real (non-`None`) encoding as keyword `name` or
    at positional `index`."""
    for kw in call.keywords:
        if kw.arg == name:
            return not _is_none(kw.value)
    return len(call.args) > index and not _is_none(call.args[index])


def _mode_kind(call: ast.Call, index: int, default: str) -> str:
    for kw in call.keywords:
        if kw.arg == "mode":
            value = kw.value
            break
    else:
        if len(call.args) <= index:
            return default
        value = call.args[index]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return _BINARY if "b" in value.value else _TEXT
    return _UNKNOWN


def _offends(call: ast.Call) -> bool:
    if _has_splat(call):
        return False

    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in _TEXT_METHODS:
        return not _supplies(call, "encoding", _TEXT_METHODS[func.attr])

    if isinstance(func, ast.Name) and func.id == "open":
        return _mode_kind(call, _OPEN_MODE_INDEX, _TEXT) == _TEXT and not _supplies(
            call, "encoding", _OPEN_ENCODING_INDEX
        )

    name = (
        func.attr
        if isinstance(func, ast.Attribute)
        else (func.id if isinstance(func, ast.Name) else None)
    )
    if name in _TEMPFILE_FACTORIES:
        mode_index, encoding_index = _TEMPFILE_FACTORIES[name]
        return _mode_kind(call, mode_index, _BINARY) == _TEXT and not _supplies(
            call, "encoding", encoding_index
        )

    return False


def violations(text: str) -> list[int]:
    """1-based lines of every unexempted text-mode call with no `encoding=`,
    anchored on the callee's end line — where a multi-line receiver's fix sits."""
    tree = ast.parse(text)
    lines = text.splitlines()

    def exempt(call: ast.Call) -> bool:
        anchors = {call.func.end_lineno or call.lineno, call.end_lineno or call.lineno}
        return any(_ANNOTATION_RE.search(lines[n - 1]) for n in anchors)

    return sorted(
        node.func.end_lineno or node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _offends(node) and not exempt(node)
    )


def _baseline_path() -> Path:
    return REPO_ROOT / "config" / "unspecified-encoding-baseline.json"


def scan_counts(files: list[str] | None = None) -> dict[str, int]:
    """{path: violation_count} for every in-scope `.py` file — repo-relative
    and walked from REPO_ROOT when FILES is omitted (a whole-tree ratchet
    scan), or exactly the given paths otherwise (pre-commit's staged-file
    list, or a test's own tmp file). A clean file is still included at 0, so
    a baseline entry that improved to zero is visible to the ratchet."""
    rels = files if files is not None else tracked_like_files(REPO_ROOT)
    counts: dict[str, int] = {}
    for rel in rels:
        path = Path(rel) if files is not None else REPO_ROOT / rel
        if path.suffix != ".py" or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        counts[rel] = len(violations(text))
    return counts


def findings(
    counts: dict[str, int], policy: dict, *, complete: bool = True
) -> list[str]:
    """Ratchet violations over a per-file violation COUNT: a new over-cap
    file, a grown or stale baseline entry — the `_ratchet` shape, `cap` from
    POLICY (0 unless the baseline overrides it)."""
    return _ratchet_findings(counts, policy, "violations", complete=complete)


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--write-baseline":
        policy = load_policy(_baseline_path())
        write_baseline(_baseline_path(), policy, scan_counts())
        return 0

    policy = load_policy(_baseline_path())
    counts = scan_counts(argv or None)
    growth = findings(counts, policy, complete=not argv)
    if growth:
        print(
            "unspecified-encoding violations (text-mode call with no "
            "encoding= — decodes with the platform default; pass "
            'encoding="utf-8", or annotate `# allow-unspecified-encoding: '
            "<reason>`):\n  " + "\n  ".join(growth),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
