#!/usr/bin/env python3
"""Generate ci-failure-notify.yaml's `workflow_run` listener list from the workflow tree.

PROBLEM CLASS — which workflows' failures reach a human at all. A post-merge or
scheduled run has no pull request to redden, so the notifier is the only surface
its red reaches; a workflow the list omits fails silently forever, and a name the
tree no longer carries matches nothing.

`workflow_run` has no wildcard and GitHub resolves the list statically from the
default branch, so it cannot be derived at run time — it is a BUILD-TIME artifact
instead. This script renders the marked region in the listener, the
`gen-failure-notify-listener` pre-commit hook keeps it current, and
ci-truth-serum's `check-failure-notifier-coverage` holds the committed region to
the same predicate as a lint. The display names come from each workflow's own
`name:` key, parsed as YAML rather than grepped, so a rename re-renders instead
of silently failing to match.

The predicate is this repo's own: EVERY workflow with a push or schedule leg,
which is a superset of the residual the lint demands (the lint requires the
workflows nothing else covers, and allows a repo to watch more). The superset is
derived from a fact this tree states — its triggers — so it needs no second copy
of the upstream routing model, and it cannot fall short of the lint when upstream
widens what counts as covered.

Run with no argument to write, or `--check` to report drift and write nothing.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# pylint: disable=wrong-import-position  # must follow the sys.path insert below
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo_root import repo_root  # noqa: E402  (path inserted just above)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from lib_marked_region import (  # noqa: E402  (path inserted just above)
    region_begin,
    region_end,
    splice,
)

# One decoded YAML object whose keys this module does not model.
YamlObject = dict[str, Any]

REPO_ROOT = repo_root(Path(__file__))
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
LISTENER = WORKFLOWS / "ci-failure-notify.yaml"

WHERE = "watched workflow names"
BEGIN = region_begin(
    WHERE,
    ".github/scripts/gen_failure_notify_listener.py",
    note="do not edit by hand",
)
END = region_end(WHERE)

# The legs whose runs have no pull request in front of them. This is the same
# pair `check_failure_notifier_coverage` reads, and the two must agree: a
# predicate wider than the lint's fails the commit that renders it.
UNATTENDED_LEGS = ("push", "schedule")


def _triggers(doc: YamlObject) -> object:
    """PyYAML reads the bareword key `on:` as the boolean True (YAML 1.1)."""
    return doc.get("on", doc.get(True))


def unattended(doc: YamlObject) -> bool:
    """True when some leg of this workflow produces runs no pull request shows."""
    triggers = _triggers(doc)
    if isinstance(triggers, str):
        return triggers in UNATTENDED_LEGS
    if isinstance(triggers, list):
        return any(leg in UNATTENDED_LEGS for leg in triggers)
    if isinstance(triggers, dict):
        return any(leg in triggers for leg in UNATTENDED_LEGS)
    return False


def workflow_paths() -> list[Path]:
    """Every workflow file but the listener itself, in a stable order."""
    found = sorted(WORKFLOWS.glob("*.yaml")) + sorted(WORKFLOWS.glob("*.yml"))
    if not found:
        raise ValueError(f"{WORKFLOWS}: holds no workflow files to derive names from")
    return [path for path in found if path != LISTENER]


def watched_names(paths: list[Path]) -> list[str]:
    """The display `name:` of every workflow the notifier must observe.

    Sorted case-insensitively, so the render does not depend on filename order.
    A workflow with no `name:` is refused: GitHub falls back to the file path as
    the display name, and a list carrying a path matches nothing the day the file
    moves.
    """
    names = set()
    for path in paths:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not unattended(doc):
            continue
        name = doc.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"{path}: has an unattended leg but no `name:` — GitHub falls back "
                "to the file path as the display name, which this list would then "
                "have to carry verbatim. Add an explicit `name:`."
            )
        names.add(name)
    if not names:
        raise ValueError(
            f"{WORKFLOWS}: no workflow has a push or schedule leg, so the rendered "
            "list would be empty and the listener would observe nothing"
        )
    return sorted(names, key=str.casefold)


def scalar(name: str) -> str:
    """NAME as a YAML scalar, quoted only where a plain one would not read back."""
    try:
        plain = yaml.safe_load(name)
    except yaml.YAMLError:
        plain = None
    if plain == name and name.strip() == name and "#" not in name:
        return name
    return json.dumps(name)  # JSON strings are valid double-quoted YAML


def render(names: list[str], indent: str) -> str:
    """The `workflows:` block, one entry per watched name."""
    entries = "\n".join(f"{indent}  - {scalar(name)}" for name in names)
    return f"{indent}workflows:\n{entries}"


def region_indent(doc: str, label: str) -> str:
    """The whitespace the region's begin marker sits at."""
    start = doc.find(BEGIN)
    if start == -1:
        raise ValueError(f"{label}: begin marker not found: {BEGIN}")
    return doc[doc.rfind("\n", 0, start) + 1 : start]


def render_listener(text: str, names: list[str]) -> str:
    """TEXT with the watched-name region re-rendered. Pure: writes no file."""
    label = str(LISTENER)
    return splice(
        text,
        begin=BEGIN,
        end=END,
        block=render(names, region_indent(text, label)),
        label=label,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift on stderr and write nothing; exit non-zero when the region is stale",
    )
    args = parser.parse_args()

    committed = LISTENER.read_text(encoding="utf-8")
    rendered = render_listener(committed, watched_names(workflow_paths()))
    if rendered == committed:
        return
    if args.check:
        raise SystemExit(
            f"{LISTENER.relative_to(REPO_ROOT)}'s watched-workflow list is stale — "
            "run: uv run python .github/scripts/gen_failure_notify_listener.py"
        )
    LISTENER.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
