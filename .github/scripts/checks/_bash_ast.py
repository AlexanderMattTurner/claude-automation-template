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
    the comment's own line, and the line below it — a site takes its
    annotation trailing on its own line or on the line above. A comment with
    no reason after MARKER exempts nothing."""
    allow = re.compile(rf"#\s*{re.escape(marker)}\s*\S")
    lines: set[int] = set()
    for node in walk(root):
        if node.type != "comment":
            continue
        if allow.search(node.text.decode()):
            line = node.start_point[0] + 1
            lines |= {line, line + 1}
    return lines
