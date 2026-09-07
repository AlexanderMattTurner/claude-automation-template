#!/usr/bin/env python3
"""Report a comment block past the prose cap in `.claude/rules/code-style.md`.

The cap is 5 lines for a note beside code and 20 for a docstring, a file
header, or the header above an exported function — the three cases
`code-style.md` names. "Exported" is language-specific: a module-level
Python function/class not prefixed `_`, a shell function, or a JavaScript
declaration carrying `export`. Only PROSE lines are charged: a blank comment
line, a delimiter, or a rule of dashes carries no words, and a vertical LIST
of distinct entries is exempt up to the block's own cap.

Comments are read with each language's own grammar (`tokenize`/`ast` for
Python, `tree-sitter-bash` for shell) so a `#` inside a string is never
mistaken for one. JavaScript has no such parser in this tree's dev
dependencies, so it falls back to a character-scanner that tracks
string/template literals — accurate for ordinary code, but it can misread a
`//`/`/*` sequence inside a regex literal.
"""

import ast
import io
import re
import sys
import token
import tokenize
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bash_ast import (  # noqa: E402  # pylint: disable=wrong-import-position
    parse as _parse_bash,
    walk,
)
from _ratchet import (  # noqa: E402  # pylint: disable=wrong-import-position
    REPO_ROOT,
    findings as _ratchet_findings,
    load_policy,
    tracked_like_files,
    write_baseline,
)

DOCSTRING = "docstring"
COMMENT = "comment"
SHELL_SUFFIXES = frozenset({".sh", ".bash"})
JS_SUFFIXES = frozenset({".mjs", ".js", ".cjs"})

# `code-style.md`: "Cap a plain comment block at 5 lines — 20 for a
# DOCSTRING, a file header, or an exported function's header."
NOTE_CAP = 5
HEADER_CAP = 20


@dataclass(frozen=True)
class Block:
    kind: str
    owned: tuple[int, ...]

    @property
    def start(self) -> int:
        return self.owned[0]

    @property
    def end(self) -> int:
        return self.owned[-1]


def _own_lines(prefix: str, row: int, end_row: int, suffix: str = "") -> set[int]:
    """The 1-based lines a comment OWNS: it shares its start/end line with code
    when text sits before the comment's start column or after its end column."""
    first = row + 1 if not prefix.strip() else row + 2
    last = end_row + 1 if not suffix.strip() else end_row
    return set(range(first, last + 1))


def _kinds_py(text: str) -> dict[int, str]:
    kinds: dict[int, str] = {}
    previous = token.NEWLINE
    statement_start = frozenset(
        {token.NEWLINE, token.INDENT, token.DEDENT, token.ENCODING}
    )
    lines = text.splitlines()
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        is_docstring = tok.type == token.STRING and previous in statement_start
        if tok.type == token.COMMENT or is_docstring:
            row, end_row = tok.start[0] - 1, tok.end[0] - 1
            owned = _own_lines(
                lines[row][: tok.start[1]], row, end_row, lines[end_row][tok.end[1] :]
            )
            kinds.update(dict.fromkeys(owned, DOCSTRING if is_docstring else COMMENT))
        if tok.type not in (token.NL, token.COMMENT):
            previous = tok.type
    return kinds


def _definitions_py(text: str) -> set[int]:
    """Module-level constants (header cap) plus exported (non-`_`-prefixed)
    module-level def/class starts, decorator included."""
    tree = ast.parse(text)
    starts = {
        node.lineno
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign)
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.name.startswith("_"):
                starts.add(min([node.lineno, *(d.lineno for d in node.decorator_list)]))
    return starts


def _kinds_sh(text: str, lines: list[str]) -> dict[int, str]:
    root = _parse_bash(text)
    kinds: dict[int, str] = {}
    for node in walk(root):
        if node.type != "comment":
            continue
        row = node.start_point[0]
        prefix = lines[row].encode()[: node.start_point[1]].decode(errors="replace")
        kinds.update(dict.fromkeys(_own_lines(prefix, row, row), COMMENT))
    return kinds


def _definitions_sh(text: str) -> set[int]:
    """A shell function's own start line is its exported surface — the whole
    unit a caller sourcing this file invokes."""
    return {
        node.start_point[0] + 1
        for node in walk(_parse_bash(text))
        if node.type == "function_definition"
    }


_JS_STRING_DELIMS = "\"'`"


def _kinds_js(text: str, lines: list[str]) -> dict[int, str]:
    """Character-scan for `//` and `/* */` comments, skipping string/template
    literals so a comment marker inside one is never mistaken for a comment."""
    kinds: dict[int, str] = {}
    i, n = 0, len(text)
    line_of = _line_index(text)
    while i < n:
        ch = text[i]
        if ch in _JS_STRING_DELIMS:
            i = _skip_string(text, i, ch)
        elif text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end == -1 else end
            row = line_of[i]
            prefix = lines[row][: i - _line_start(text, i)]
            kinds.update(dict.fromkeys(_own_lines(prefix, row, row), COMMENT))
            i = end
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            close = n - 2 if close == -1 else close
            row, end_row = line_of[i], line_of[close]
            prefix = lines[row][: i - _line_start(text, i)]
            end_col = close + 2 - _line_start(text, close)
            suffix = lines[end_row][end_col:] if end_row < len(lines) else ""
            kind = DOCSTRING if text.startswith("/**", i) else COMMENT
            kinds.update(dict.fromkeys(_own_lines(prefix, row, end_row, suffix), kind))
            i = close + 2
        else:
            i += 1
    return kinds


def _skip_string(text: str, i: int, delim: str) -> int:
    i += 1
    n = len(text)
    while i < n and text[i] != delim:
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _line_index(text: str) -> list[int]:
    """`row` for each character offset, 0-based."""
    lines = text.split("\n")
    index = []
    for row, line in enumerate(lines):
        index.extend([row] * (len(line) + 1))
    return index[: len(text)]


def _line_start(text: str, offset: int) -> int:
    start = text.rfind("\n", 0, offset)
    return start + 1


_JS_EXPORT_DEF_RE = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\b|class\b)|"
    r"^export\s+(?:const|let|var)\s+\w"
)


def _definitions_js(text: str) -> set[int]:
    """Any top-level (column-0) `export ...` declaration line."""
    return {
        n
        for n, line in enumerate(text.splitlines(), start=1)
        if _JS_EXPORT_DEF_RE.match(line)
    }


def _blocks(kinds: dict[int, str]) -> list[Block]:
    blocks: list[Block] = []
    run: list[int] = []
    for line in sorted(kinds):
        if run and line != run[-1] + 1:
            blocks.append(_block(run, kinds))
            run = []
        run.append(line)
    if run:
        blocks.append(_block(run, kinds))
    return blocks


def _block(run: list[int], kinds: dict[int, str]) -> Block:
    kind = DOCSTRING if any(kinds[n] == DOCSTRING for n in run) else COMMENT
    return Block(kind=kind, owned=tuple(run))


_INTRODUCERS = (
    ("///", False),
    ("/**", False),
    ('"""', True),
    ("'''", True),
    ("//", False),
    ("*/", False),
    ("/*", False),
    ("#", False),
)
_GUTTER = ("*", False)
_BULLETS = "-*+•·"
_MARKER_RE = re.compile(
    rf"^(?:[{re.escape(_BULLETS)}]|\(?[0-9]{{1,3}}[.)]|\(?[a-z][.)])\s+\S"
)
_ANNOTATION_RE = re.compile(r"^[a-z][a-z-]*-ok:\s*\S|^allow-[a-z-]+:\s*\S")


def _body(line: str, *, gutter: bool) -> str:
    lead = len(line) - len(line.lstrip())
    rest = line[lead:]
    candidates = (*_INTRODUCERS, _GUTTER) if gutter else _INTRODUCERS
    for introducer, keeps_lead in candidates:
        if rest.startswith(introducer):
            body = rest[len(introducer) :].removeprefix(" ")
            return " " * lead + body if keeps_lead else body
    return line


def _is_prose(text: str) -> bool:
    return any(character.isalnum() for character in text)


def _charged(block: Block, lines: list[str], suffix: str, cap: int) -> int:
    gutter = suffix in JS_SUFFIXES
    bodies = (_body(lines[n - 1], gutter=gutter) for n in block.owned)
    prose = [text for text in bodies if _is_prose(text)]
    if not prose:
        return 0
    indents = [len(text) - len(text.lstrip()) for text in prose]
    margin = min(indents)
    charged = 0
    openers = 0
    entry_indent: int | None = None
    for text, indent in zip(prose, indents, strict=True):
        stripped = text.strip()
        marked = bool(_MARKER_RE.match(stripped))
        if indent <= margin and not marked and not _ANNOTATION_RE.match(stripped):
            charged += 1
            entry_indent = None
        elif marked or entry_indent is None or indent <= entry_indent:
            openers += 1
            entry_indent = indent
    return charged + max(0, openers - cap)


def _content_line(lines: list[str], after: int) -> int:
    for number in range(after + 1, len(lines) + 1):
        if lines[number - 1].strip():
            return number
    return 0


def _cap(block: Block, lines: list[str], definition_lines: frozenset[int]) -> int:
    shebang = 1 if lines and lines[0].startswith("#!") else 0
    heads = (
        block.kind == DOCSTRING
        or block.start == _content_line(lines, shebang)
        or _content_line(lines, block.end) in definition_lines
    )
    return HEADER_CAP if heads else NOTE_CAP


def _parse(text: str, suffix: str) -> tuple[list[Block], frozenset[int]]:
    lines = text.splitlines()
    if suffix == ".py":
        kinds = _kinds_py(text)
        definitions = frozenset(_definitions_py(text))
    elif suffix in SHELL_SUFFIXES:
        kinds = _kinds_sh(text, lines)
        definitions = frozenset(_definitions_sh(text))
    elif suffix in JS_SUFFIXES:
        kinds = _kinds_js(text, lines)
        definitions = frozenset(_definitions_js(text))
    else:
        return [], frozenset()
    if lines and lines[0].startswith("#!"):
        kinds.pop(1, None)
    return _blocks(kinds), definitions


def find_violations(text: str, suffix: str) -> list[int]:
    """The 1-based start line of every comment block past its cap in a file
    of the given suffix (empty for a language this check does not read)."""
    blocks, definitions = _parse(text, suffix)
    lines = text.splitlines()
    hits = []
    for block in blocks:
        cap = _cap(block, lines, definitions)
        if _charged(block, lines, suffix, cap) > cap:
            hits.append(block.start)
    return hits


_SCANNED_SUFFIXES = frozenset({".py", *SHELL_SUFFIXES, *JS_SUFFIXES})


def _baseline_path() -> Path:
    return REPO_ROOT / "config" / "comment-block-length-baseline.json"


def scan_counts(files: list[str] | None = None) -> dict[str, int]:
    """{path: violation_count} for every in-scope file — repo-relative and
    walked from REPO_ROOT when FILES is omitted (a whole-tree ratchet scan),
    or exactly the given paths otherwise (pre-commit's staged-file list, or a
    test's own tmp file). A clean file is still included at 0, so a baseline
    entry that improved to zero is visible to the ratchet as stale."""
    rels = files if files is not None else tracked_like_files(REPO_ROOT)
    counts: dict[str, int] = {}
    for rel in rels:
        path = Path(rel) if files is not None else REPO_ROOT / rel
        if path.suffix not in _SCANNED_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        counts[rel] = len(find_violations(text, path.suffix))
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
            "comment-block-length violations (code-style.md caps 5 lines "
            "note / 20 header):\n  " + "\n  ".join(growth),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
