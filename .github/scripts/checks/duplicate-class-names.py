#!/usr/bin/env python3
"""Report a top-level class name defined in more than one `.github/scripts` module.

Two modules defining the same class name means neither is the definition, and
a call site reads one header while getting the other's behaviour.

Scope is tracked `.py` files under `.github/scripts/`, excluding tests: the CI
estate is one body of code maintained together, so a repeated name there is
drift. A DEFINITION is a MODULE-LEVEL `ast.ClassDef`; a class nested in a
function or another class is scoped by its enclosing name and never collides.
Exempt a definition with `# allow-duplicate-class: <reason>` on its `class`
line — the annotated file stops being reported, and every other file defining
that name still is.

No grandfathered baseline: every collision found is reported, since a repo
this check is newly added to has none to spend.
"""

import ast
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SCOPE = ".github/scripts"

_ANNOTATION_RE = re.compile(r"#\s*allow-duplicate-class:\s*\S")


def _scanned_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", SCOPE], capture_output=True, check=True
    ).stdout.decode()
    return [
        rel
        for rel in out.split("\0")
        if rel.endswith(".py")
        and Path(rel).is_file()
        and "/tests/" not in rel
        and not Path(rel).name.startswith("test_")
    ]


@dataclass(frozen=True, slots=True)
class ModuleClasses:
    """One module's top-level class names, and which of them opted out.

    `defined` holds every name, annotated ones included, because a name's
    presence is what makes another module's copy a collision; `exempt` is the
    subset this module is not reported for.
    """

    defined: tuple[str, ...]
    exempt: frozenset[str]


def top_level_classes(source: str) -> ModuleClasses:
    lines = source.splitlines()
    defined: list[str] = []
    exempt: set[str] = set()
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef):
            continue
        defined.append(node.name)
        if _ANNOTATION_RE.search(lines[node.lineno - 1]):
            exempt.add(node.name)
    return ModuleClasses(tuple(defined), frozenset(exempt))


def find_collisions(classes_by_file: dict[str, ModuleClasses]) -> dict[str, list[str]]:
    """{rel: [colliding class names]} — a name collides when at least one
    OTHER file in the mapping defines it too, and this file did not exempt it."""
    files_by_name: dict[str, set[str]] = defaultdict(set)
    for rel, classes in classes_by_file.items():
        for name in classes.defined:
            files_by_name[name].add(rel)
    return {
        rel: sorted(
            {
                name
                for name in classes.defined
                if len(files_by_name[name]) > 1 and name not in classes.exempt
            }
        )
        for rel, classes in classes_by_file.items()
    }


def scan_tree() -> dict[str, list[str]]:
    classes_by_file = {
        rel: top_level_classes(Path(rel).read_text(encoding="utf-8"))
        for rel in _scanned_files()
    }
    return find_collisions(classes_by_file)


def main() -> int:
    collisions = {rel: names for rel, names in scan_tree().items() if names}
    if not collisions:
        return 0
    lines = [f"{rel}: {', '.join(names)}" for rel, names in sorted(collisions.items())]
    print(
        "duplicate-class-name violations — the same top-level class name in two "
        "modules leaves neither as the definition, so a call site reads one "
        "header and gets the other's behaviour. Make one module the "
        "definition and import it, rename the one that means something else, "
        "or annotate a deliberate repeat with `# allow-duplicate-class: <reason>` "
        "on the `class` line:\n  " + "\n  ".join(lines),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
