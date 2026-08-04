"""Every path a synced shell file names must be a path the sync delivers.

template-sync copies the entries in ``SYNC_PATHS`` and nothing else. A synced
script that names a template file OUTSIDE those entries therefore arrives
downstream without it, and dies on the read — far from the cause, in a message
that names neither the sync nor the missing entry. Three of these shipped:
``install-mergiraf.sh`` and ``install-claude-cli.sh`` reading their version pins
under ``.github/``, and ``.hooks/commit-msg`` passing commitlint a config under
``config/`` that no consumer received unless it happened to be seeded with one.

A reference is legitimate when the referenced file is CONSUMER-owned: the
consumer supplies it and the synced reader tolerates its absence. Mark those with
``# allow-unsynced: <path> — <reason>``. The annotation must NAME the path it
excuses, so one exemption cannot blind the rest of the file.
"""

import functools
import re
import subprocess
from pathlib import Path

import pytest
import tree_sitter_bash
from tree_sitter import Language, Parser

from ._helpers import REPO_ROOT

TEMPLATE_SYNC_YAML = REPO_ROOT / ".github/workflows/template-sync.yaml"
ALLOW = "allow-unsynced:"

# Any slash-bearing token, optionally prefixed by the ${VAR}/ a script uses to
# anchor itself at its own directory. Deliberately loose: this only nominates
# CANDIDATES, and membership in the tracked-file set is what decides. Requiring a
# dotted extension here is what a first draft did, and it silently missed both
# `claude-cli-version` (no extension) and `commitlint.config.js` (two dots) — a
# hole invisible while the tree was green.
PATH_LITERAL = re.compile(r"(?:\$\{\w+\}/)?[\w.\-/]*/[\w.\-]+")


@functools.cache
def tracked_files() -> frozenset[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return frozenset(out.split())


@functools.cache
def path_list(name: str) -> tuple[str, ...]:
    """One of the workflow's space-separated path lists."""
    text = TEMPLATE_SYNC_YAML.read_text(encoding="utf-8")
    match = re.search(rf'^\s*{name}:\s*"(?P<paths>[^"]*)"', text, re.MULTILINE)
    assert match, f"template-sync.yaml declares {name}"
    return tuple(match.group("paths").split())


def sync_paths() -> tuple[str, ...]:
    return path_list("SYNC_PATHS")


def is_delivered(path: str) -> bool:
    """Whether every consumer receives this file, modelling template-sync.sh.

    Coverage by SYNC_PATHS is necessary but not sufficient. The sync subtracts
    two sets, both matched EXACTLY (`is_excluded`, `is_opt_in`), and a guard
    whose model is more permissive than the sync reintroduces the very
    fail-open it exists to close — one level down, where only a downstream
    break would ever reveal it.
    """
    covered = next(
        (e for e in sync_paths() if path == e or path.startswith(e + "/")), None
    )
    if covered is None:
        return False
    excluded = path_list("EXCLUDE_PATHS")
    # An opt-in file is never INTRODUCED, so a consumer without it stays without
    # it — undeliverable for a reader that needs it present.
    return (
        covered not in excluded
        and path not in excluded
        and path not in path_list("OPT_IN_PATHS")
    )


def synced_shell_files() -> list[str]:
    """Synced files a shell interprets: an extension, or a hook under .hooks/."""
    return sorted(
        f
        for f in tracked_files()
        if is_delivered(f) and (f.endswith((".sh", ".bash")) or f.startswith(".hooks/"))
    )


def code_only(source: str) -> str:
    """Blank every comment, keeping byte offsets so line numbers still hold.

    A path named in a comment is prose, not a reference. Where a comment starts
    is a question about the grammar — a `#` inside a string or a heredoc body
    starts nothing — so the bash parser answers it rather than a scan.
    """
    parser = Parser(Language(tree_sitter_bash.language()))
    tree = parser.parse(source.encode("utf-8"))
    out = bytearray(source.encode("utf-8"))
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "comment":
            for i in range(node.start_byte, node.end_byte):
                if out[i] != ord("\n"):
                    out[i] = ord(" ")
            continue
        stack.extend(node.children)
    return out.decode("utf-8")


def _resolutions(containing_file: str, literal: str) -> set[str]:
    """The repo-relative paths a literal could denote: repo-rooted, or file-relative."""
    bare = re.sub(r"^\$\{\w+\}/", "", literal)
    resolved = {bare}
    anchored = (REPO_ROOT / Path(containing_file).parent / bare).resolve()
    if anchored.is_relative_to(REPO_ROOT):
        resolved.add(str(anchored.relative_to(REPO_ROOT)))
    return resolved


def undelivered_in_source(containing_file: str, source: str) -> list[tuple[int, str]]:
    """(line, referenced path) for each undelivered template file the code names."""
    excused = {
        path
        for line in source.splitlines()
        if ALLOW in line
        for path in PATH_LITERAL.findall(line)
    }
    found = []
    for lineno, line in enumerate(code_only(source).splitlines(), start=1):
        for literal in PATH_LITERAL.findall(line):
            for candidate in _resolutions(containing_file, literal):
                if (
                    candidate in tracked_files()
                    and not is_delivered(candidate)
                    and candidate not in excused
                ):
                    found.append((lineno, candidate))
    return found


@pytest.mark.parametrize("path", synced_shell_files())
def test_synced_shell_names_only_delivered_paths(path: str) -> None:
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    offenders = undelivered_in_source(path, source)
    assert not offenders, (
        f"{path} is synced to every consumer, but names template files that "
        "SYNC_PATHS does not deliver: "
        + ", ".join(f"{ref} (line {line})" for line, ref in offenders)
        + f". Add the path to SYNC_PATHS in {TEMPLATE_SYNC_YAML.name}, or mark it "
        f"`# {ALLOW} <path> — <reason>` when the consumer owns that file and this "
        "reader tolerates its absence."
    )


def undelivered_examples() -> list[str]:
    """Real tracked paths the sync does not deliver — what the check exists for.

    Restricted to slash-bearing paths, the only shape a reference can take.
    """
    candidates = sorted(
        f for f in tracked_files() if not is_delivered(f) and PATH_LITERAL.fullmatch(f)
    )
    assert len(candidates) >= 2, "the template tracks undelivered files to test against"
    return candidates


def undelivered_example() -> str:
    return undelivered_examples()[0]


def test_an_undelivered_reference_in_code_is_caught() -> None:
    target = undelivered_example()
    assert undelivered_in_source(
        ".hooks/probe.sh", f'#!/bin/bash\ncat "{target}"\n'
    ) == [(2, target)]


def test_the_same_path_in_a_comment_is_not_a_reference() -> None:
    target = undelivered_example()
    assert (
        undelivered_in_source(".hooks/probe.sh", f"#!/bin/bash\n# see {target}\n") == []
    )


def test_an_annotation_excuses_only_the_path_it_names() -> None:
    target = undelivered_example()
    source = f'#!/bin/bash\n# {ALLOW} {target} — consumer-owned\ncat "{target}"\n'
    assert undelivered_in_source(".hooks/probe.sh", source) == []

    other = undelivered_examples()[1]
    blind = f'#!/bin/bash\n# {ALLOW} {target} — consumer-owned\ncat "{other}"\n'
    assert undelivered_in_source(".hooks/probe.sh", blind) == [(3, other)]


def test_delivery_subtracts_excluded_and_opt_in_paths() -> None:
    """A path under SYNC_PATHS is not delivered if the sync then skips it.

    Pins the three subtractions template-sync.sh makes, against the live lists.
    """
    assert is_delivered(".github/scripts/install-mergiraf.sh")

    for excluded in path_list("EXCLUDE_PATHS"):
        assert not is_delivered(excluded), f"{excluded} is in EXCLUDE_PATHS"
    for opt_in in path_list("OPT_IN_PATHS"):
        assert not is_delivered(opt_in), f"{opt_in} is in OPT_IN_PATHS"

    # Excluding a whole SYNC_PATHS entry withdraws everything beneath it, which
    # is the case an exact-match-only reading of EXCLUDE_PATHS would miss.
    assert ".github/prompts" not in path_list("EXCLUDE_PATHS")
    excluded_prompt = next(
        p for p in path_list("EXCLUDE_PATHS") if p.startswith(".github/prompts/")
    )
    assert not is_delivered(excluded_prompt)
