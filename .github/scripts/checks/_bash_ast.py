"""Shared tree-sitter-bash reading for the shell lints under this directory.

PROBLEM CLASS — every shell lint here asks the bash grammar the same three
questions: the static text of a word, the ``[name, *args]`` of a simple
command, and the lines an ``# <marker> <reason>`` annotation exempts. A copy
per lint drifts on the quoting forms it resolves and on how wide the
annotation window is, so each lint answers "is this suppressed?" differently.

Imported as a sibling: a lint runs as ``python .github/scripts/checks/x.py``,
so this directory is ``sys.path[0]``; the tests load each lint by path, so
each prepends its own directory to ``sys.path`` before importing this module.
"""

import re
from collections.abc import Iterator

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

_PARSER = Parser(Language(tree_sitter_bash.language()))


def parse(text: str) -> Node:
    """The root node of TEXT read as bash."""
    return _PARSER.parse(text.encode()).root_node


def walk(node: Node) -> Iterator[Node]:
    """NODE and every node below it."""
    yield node
    for child in node.children:
        yield from walk(child)


def literal(node: Node) -> str | None:
    """The static text of a word, or None when an expansion decides it at run time."""
    if node.type in ("word", "number"):
        return node.text.decode()
    if node.type == "raw_string":
        return node.text.decode()[1:-1]
    if node.type == "string" and all(
        c.type == "string_content" for c in node.children[1:-1]
    ):
        return node.text.decode()[1:-1]
    return None


def command_words(node: Node) -> list[str | None] | None:
    """``[name, *args]`` for a ``command`` node, or None when the stage is not
    a plain command or its name is built from an expansion. An argument this
    module cannot read literally is None, so a caller sees its position."""
    if node.type != "command":
        return None
    name_node = node.child_by_field_name("name")
    if name_node is None or not name_node.children:
        return None
    name = literal(name_node.children[0])
    if name is None:
        return None
    args = [literal(c) for c in node.children_by_field_name("argument")]
    return [name, *args]


def suppressed_lines(root: Node, marker: str) -> set[int]:
    """The 1-based lines a ``# MARKER <reason>`` comment exempts under ROOT:
    the comment's own line, and the first line below it that is not itself a
    comment. A comment with no reason after MARKER exempts nothing.

    PROBLEM CLASS — a one-line window silently stops covering its site the
    moment a SECOND annotation is written above it, because the site now sits
    two lines down. A site needing two markers is normal (a download is both
    unretried and unpinned), and the failure reads as a fresh violation of the
    check whose annotation lost the race. Walking past the comment block is
    what makes annotation ORDER stop mattering, and it matches the window
    ci-truth-serum's ``_linecheck.annotation_window`` already uses.
    """
    allow = re.compile(rf"#\s*{re.escape(marker)}\s*\S")
    source = root.text.decode().splitlines()
    # Only a WHOLE-line comment is walked past. A comment trailing real code
    # sits on the site's own line, so skipping it would step over the very line
    # the annotation above it is there to cover.
    whole_line_comments = {
        node.start_point[0] + 1
        for node in walk(root)
        if node.type == "comment"
        and source[node.start_point[0]].lstrip().startswith("#")
    }
    lines: set[int] = set()
    for node in walk(root):
        if node.type != "comment":
            continue
        if not allow.search(node.text.decode()):
            continue
        line = node.start_point[0] + 1
        lines.add(line)
        below = line + 1
        while below in whole_line_comments:
            below += 1
        lines.add(below)
    return lines
