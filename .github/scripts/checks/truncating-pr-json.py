#!/usr/bin/env python3
"""Refuse a ``gh pr view``/``gh pr list`` that reads a TRUNCATING connection field.

``gh``'s ``--json`` flag builds a GraphQL query whose connection fields are asked
for as ``<field>(first: 100)`` — no cursor, no ``pageInfo``. Past 100 entries
GitHub returns 100 and ``gh`` exits 0 with a well-formed list, and nothing in the
response says so, so no layer can warn.

SECURITY/CORRECTNESS INVARIANT: this refusal is what stops a consumer
re-introducing a read that is silently short — a token decided from a truncated
``files`` list, an age window decided from the OLDEST 100 ``commits``.

Flagged fields are the connections with no natural bound in this repo —
``files``, ``commits``, ``comments``, ``reviews``. ``labels`` and ``assignees``
are bounded by what the repository defines, so they cannot reach 100.

A ``--json`` whose value is an expansion (``--json "$fields"``) is NOT flagged:
the field set is not knowable here, and guessing would either miss it or
false-positive on every caller.

A site that must keep a truncating read opts out with
``# truncating-pr-json-ok: <reason>`` on the command's line or the line above.

Invoked by pre-commit with the staged shell files as arguments.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _bash_ast import (  # noqa: E402  # pylint: disable=wrong-import-position
    command_words,
    parse,
    suppressed_lines,
    walk,
)
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ALLOW = "truncating-pr-json-ok:"

# Connection fields `gh` asks for as `<field>(first: 100)` that a PR here can
# exceed (many check runs, a PR touching over 100 files).
_TRUNCATING = frozenset({"files", "commits", "comments", "reviews"})

_MESSAGE = (
    "`gh pr view/list --json` reads a connection gh caps at 100 with no cursor — "
    "the short list arrives with exit 0 and nothing says it was cut. Read the "
    "paging REST endpoint (`gh api --paginate repos/{owner}/{repo}/pulls/N/files`), "
    "or annotate `# truncating-pr-json-ok: <reason>`."
)


def _requested_fields(words: list[str | None]) -> set[str]:
    """The field names a literal ``--json`` asks for. An expansion contributes
    nothing: its value is decided at run time."""
    fields: set[str] = set()
    for i, word in enumerate(words):
        if word is None:
            continue
        if word.startswith("--json="):
            raw = word.removeprefix("--json=")
        elif word == "--json":
            raw = words[i + 1] if i + 1 < len(words) and words[i + 1] else ""
        else:
            continue
        raw = raw.strip("\"'")
        if "$" in raw or "`" in raw:
            continue
        fields |= {name.strip() for name in raw.split(",")}
    return fields


def _truncating_read(words: list[str | None]) -> bool:
    if words[0] != "gh":
        return False
    if "pr" not in words or not ({"view", "list"} & set(words)):
        return False
    return bool(_requested_fields(words) & _TRUNCATING)


def violations(text: str) -> list[int]:
    """1-based line numbers reading a truncating ``--json`` connection field,
    unannotated."""
    root = parse(text)
    exempt = suppressed_lines(root, _ALLOW)
    hits: list[int] = []
    for node in walk(root):
        words = command_words(node)
        if not words or not _truncating_read(words):
            continue
        line = node.start_point[0] + 1
        if line not in exempt:
            hits.append(line)
    return sorted(hits)


def main(argv: list[str]) -> None:
    sys.exit(run_line_checks(argv, violations, _MESSAGE))


if __name__ == "__main__":
    main(sys.argv[1:])
