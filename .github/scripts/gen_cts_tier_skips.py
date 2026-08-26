#!/usr/bin/env python3
"""Generate each ci-truth-serum tier aggregate's `--skip` list into .pre-commit-config.yaml.

PROBLEM CLASS — a hook's argument list restates a fact its own config already
states, so the two drift and only a lockstep test notices.

An aggregate hook (`check-tier1` / `check-tier2` / `check-extras`) runs every
member of its tier. This repo also lists some of those members as standalone
hooks, because a standalone entry can carry arguments the aggregate cannot pass
(`check-failure-notifier-coverage --require-notifier`). Every separately-listed
member must therefore be skipped by its aggregate, or the whole config runs it
twice. That skip list is a DERIVED value with two sources, and neither is this
file's to invent:

- which hook ids this repo lists on its own, read from `.pre-commit-config.yaml`;
- which tier each member belongs to, read from `_registry.CHECKS` in the
  ci-truth-serum clone at the `rev:` this config pins.

pre-commit reads `args` inline and cannot compute them, which is the hard
constraint that makes a generated region the right shape here — the same one
`rev:` already lives under. `run_tier` rejects a skip name outside its own tier,
so a hand-typed list fails the commit rather than silently over-skipping.
"""

import argparse
import ast
import contextlib
import os
import sqlite3
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
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
CTS_SUFFIX = "/ci-truth-serum"
# The `args:` list items sit two levels under the hook entry's dash.
INDENT = " " * 10


def begin_marker(hook_id: str) -> str:
    """The opening marker line for HOOK_ID's skip region, without indentation."""
    return region_begin(
        where(hook_id),
        ".github/scripts/gen_cts_tier_skips.py",
        note="do not edit by hand",
    )


def end_marker(hook_id: str) -> str:
    """The closing marker line for HOOK_ID's skip region, without indentation."""
    return region_end(where(hook_id))


def where(hook_id: str) -> str:
    """The region label both marker lines repeat."""
    return f"{hook_id} skips"


def cts_repo(config: YamlObject) -> YamlObject:
    """The one ci-truth-serum entry in `repos:`."""
    found = [
        repo
        for repo in config["repos"]
        if str(repo.get("repo", "")).rstrip("/").endswith(CTS_SUFFIX)
    ]
    if len(found) != 1:
        raise ValueError(
            f"{CONFIG}: expected exactly one ci-truth-serum repo entry, found "
            f"{len(found)} — the skip lists are derived from that block's hook ids"
        )
    return found[0]


def tier_of(hook_id: str, tiers: dict[str, list[str]]) -> str | None:
    """The `run_tier.TIERS` key an aggregate's hook id names, or None.

    Upstream keys a numbered tier "1"/"2" and names the odd one "extras", which
    this config spells `check-tier1` and `check-extras`.
    """
    for key in tiers:
        if hook_id == (f"check-tier{key}" if key.isdigit() else f"check-{key}"):
            return key
    return None


def module_name(hook_id: str) -> str:
    """The `ci_truth_serum` module a hook id names — `--skip` takes that spelling."""
    return hook_id.replace("-", "_")


def skips_by_hook(
    config: YamlObject, tiers: dict[str, list[str]]
) -> dict[str, list[str]]:
    """{aggregate hook id: the members it must skip, in tier order}.

    A member listed on its own is skipped by the aggregate that owns it and by no
    other: `run_tier` exits 2 on a skip name outside the tier it is given.
    """
    hooks = cts_repo(config)["hooks"]
    ids = [hook["id"] for hook in hooks]
    if len(ids) != len(set(ids)):
        raise ValueError(
            f"{CONFIG}: the ci-truth-serum block lists a hook id twice, so an "
            "aggregate cannot be told which entry carries the generated skips"
        )
    aggregates = {i: tier_of(i, tiers) for i in ids}
    standalone = {module_name(i) for i, tier in aggregates.items() if tier is None}
    return {
        hook_id: [m for m in tiers[tier] if m in standalone]
        for hook_id, tier in aggregates.items()
        if tier is not None
    }


def render_block(names: list[str]) -> str:
    """The generated region's bytes: one `- --skip` / `- <name>` pair per line."""
    return "\n".join(f"{INDENT}- --skip\n{INDENT}- {name}" for name in names)


def _drop_emptied_args(text: str, begin: str, end: str, label: str) -> str:
    """TEXT with the whole `args:` key removed, because its region now derives
    no skips — `yaml.safe_load` reads an empty `args:` as null, and pre-commit
    then passes the hook no argv at all, silently running the whole tier.

    Requires an `args:` line directly above the region: that is the shape
    every generated region here is written in, so anything else means the
    file was hand-edited and a human should look rather than have this
    generator guess what to delete.
    """
    lines = text.splitlines(keepends=True)
    begin_idx = next(i for i, ln in enumerate(lines) if begin in ln)
    end_idx = next(i for i in range(begin_idx + 1, len(lines)) if end in lines[i])
    args_idx = begin_idx - 1
    if lines[args_idx].strip() != "args:":
        raise ValueError(
            f"{label}: expected an `args:` line directly above the region to "
            f"remove now that it derives no skips, found: {lines[args_idx].strip()!r}"
        )
    return "".join(lines[:args_idx] + lines[end_idx + 1 :])


def render_config(text: str, tiers: dict[str, list[str]]) -> str:
    """TEXT with every aggregate's skip region rewritten. Pure: writes no file.

    An aggregate that skips nothing carries no `args:` and no region, so it is
    passed over; one that skips something and has no region is a refusal, which
    `splice` raises. One that HAD a region and now derives no skips has its
    whole `args:` block removed, rather than left with an empty list.
    """
    config = yaml.safe_load(text)
    for hook_id, names in skips_by_hook(config, tiers).items():
        begin, end = begin_marker(hook_id), end_marker(hook_id)
        label = f"{CONFIG}: {where(hook_id)}"
        if not names:
            if begin in text:
                text = _drop_emptied_args(text, begin, end, label)
            continue
        text = splice(
            text, begin=begin, end=end, block=render_block(names), label=label
        )
    return text


def _store_db() -> Path:
    """pre-commit's clone index — the same one it resolves a `rev:` through."""
    home = os.environ.get("PRE_COMMIT_HOME")
    if home:
        return Path(home) / "db.db"
    cache = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(cache) / "pre-commit" / "db.db"


def cloned_repo(url: str, rev: str) -> Path:
    """The directory pre-commit cloned URL at REV into.

    Reading the tiers out of the pinned clone is what keeps this generator and
    the hooks it configures on ONE definition of tier membership. The refusal
    below is the point: a missing clone means the derived list would be a guess.
    """
    db = _store_db()
    if not db.is_file():
        raise SystemExit(
            f"no pre-commit store at {db} — run `pre-commit install-hooks` so the "
            "pinned ci-truth-serum clone this generator reads its tiers from exists"
        )
    with contextlib.closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT path FROM repos WHERE repo = ? AND ref = ?", (url, rev)
        ).fetchall()
    if not rows:
        raise SystemExit(
            f"pre-commit has not cloned {url} at {rev} — run `pre-commit "
            "install-hooks` before regenerating the tier skip lists"
        )
    return Path(rows[0][0])


def parse_tiers(source: str) -> dict[str, list[str]]:
    """{tier key: its member module names} from `_registry.py`'s `CHECKS` tuple.

    `TIERS` itself is a dict comprehension over `CHECKS` (module, tier, kind,
    *tags), not a literal — that shape cannot be read back by AST without
    evaluating the comprehension. `CHECKS` is the literal SSOT it derives from:
    each element is a `_check(module, tier, ...)` call whose first two
    positional args are string constants. Parsed rather than imported: the
    third positional arg is a module CONSTANT (e.g. `WORKFLOW`), and importing
    to resolve it would drag in `identify` and the rest of the hook's own
    environment for a value this generator never reads.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "CHECKS" for t in targets):
            continue
        tiers: dict[str, list[str]] = {}
        for call in node.value.elts:
            module, tier = call.args[0].value, call.args[1].value
            tiers.setdefault(tier, []).append(module)
        return tiers
    raise ValueError("_registry.py declares no CHECKS tuple")


def tiers_from_pin(config: YamlObject) -> dict[str, list[str]]:
    """The tier registry of the ci-truth-serum revision this config pins."""
    repo = cts_repo(config)
    clone = cloned_repo(repo["repo"], repo["rev"])
    return parse_tiers(
        (clone / "ci_truth_serum" / "_registry.py").read_text(encoding="utf-8")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift on stderr and write nothing; exit non-zero when a region is stale",
    )
    args = parser.parse_args()

    current = CONFIG.read_text(encoding="utf-8")
    updated = render_config(current, tiers_from_pin(yaml.safe_load(current)))
    if current == updated:
        return
    if args.check:
        raise SystemExit(
            f"{CONFIG.name}'s tier skip lists are stale against the hook ids it "
            "lists — run: uv run python .github/scripts/gen_cts_tier_skips.py"
        )
    CONFIG.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
