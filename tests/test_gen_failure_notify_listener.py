""".github/scripts/gen_failure_notify_listener.py — the notifier's watched-name list.

Drives the generator's pure predicate and renderer over synthetic workflow
documents, then round-trips the real tree: the committed region must already
equal what the tree implies, because a workflow the list omits has no surface
its failure reaches.
"""

import importlib.util

import pytest
import yaml

from tests._helpers import REPO_ROOT

_SRC = REPO_ROOT / ".github" / "scripts" / "gen_failure_notify_listener.py"
_spec = importlib.util.spec_from_file_location("gen_failure_notify_listener", _SRC)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

LISTENER = f"""\
on:
  workflow_run:
    {mod.BEGIN}
    workflows:
      - Stale
    {mod.END}
"""


@pytest.mark.parametrize(
    ("triggers", "watched"),
    [
        ("on:\n  push:\n    branches: [main]", True),
        ("on:\n  schedule:\n    - cron: '0 0 * * *'", True),
        ("on:\n  pull_request:\n    types: [opened]", False),
        ("on: push", True),
        ("on: pull_request", False),
        ("on: [pull_request, schedule]", True),
        ("on: [pull_request, issues]", False),
        ("on:\n  workflow_dispatch:", False),
    ],
)
def test_unattended_reads_every_trigger_shape(triggers: str, watched: bool) -> None:
    assert mod.unattended(yaml.safe_load(triggers)) is watched


def workflow(name: str, trigger: str = "push") -> str:
    return f"name: {name}\non:\n  {trigger}:\n"


def write_tree(tmp_path, docs: dict[str, str]) -> list:
    for filename, text in docs.items():
        (tmp_path / filename).write_text(text, encoding="utf-8")
    return sorted(tmp_path.glob("*.yaml"))


def test_names_are_sorted_case_insensitively(tmp_path) -> None:
    paths = write_tree(
        tmp_path,
        {
            "a.yaml": workflow("zizmor"),
            "b.yaml": workflow("Lint"),
            "c.yaml": workflow("pre-commit", trigger="schedule"),
        },
    )
    assert mod.watched_names(paths) == ["Lint", "pre-commit", "zizmor"]


def test_pull_request_only_workflows_are_left_out(tmp_path) -> None:
    paths = write_tree(
        tmp_path,
        {"a.yaml": workflow("Kept"), "b.yaml": workflow("Dropped", "pull_request")},
    )
    assert mod.watched_names(paths) == ["Kept"]


def test_a_watched_workflow_without_a_name_is_refused(tmp_path) -> None:
    paths = write_tree(tmp_path, {"a.yaml": "on:\n  push:\n"})
    with pytest.raises(ValueError, match="no `name:`"):
        mod.watched_names(paths)


def test_a_tree_with_nothing_unattended_is_refused(tmp_path) -> None:
    paths = write_tree(tmp_path, {"a.yaml": workflow("Only PRs", "pull_request")})
    with pytest.raises(ValueError, match="observe nothing"):
        mod.watched_names(paths)


def test_render_replaces_the_region_and_is_idempotent() -> None:
    once = mod.render_listener(LISTENER, ["Lint", "zizmor"])
    assert "- Stale" not in once
    assert "    workflows:\n      - Lint\n      - zizmor\n" in once
    assert mod.render_listener(once, ["Lint", "zizmor"]) == once


def test_a_name_yaml_would_misread_is_quoted() -> None:
    out = mod.render_listener(LISTENER, ["Lint", "on", "yes: no"])
    assert '- "on"' in out and '- "yes: no"' in out


def test_missing_marker_raises_instead_of_writing_nothing() -> None:
    with pytest.raises(ValueError, match="begin marker not found"):
        mod.render_listener("on:\n  workflow_run:\n", ["Lint"])


def test_real_tree_round_trips_and_is_idempotent() -> None:
    committed = mod.LISTENER.read_text(encoding="utf-8")
    names = mod.watched_names(mod.workflow_paths())
    once = mod.render_listener(committed, names)
    assert once == committed
    assert mod.render_listener(once, names) == once


def test_real_region_equals_the_list_github_reads() -> None:
    """The parsed list, not the rendered text — a marker that moved renders in the
    wrong place, and only reading the document back catches that."""
    doc = yaml.safe_load(mod.LISTENER.read_text(encoding="utf-8"))
    listed = doc[True]["workflow_run"]["workflows"]
    assert listed == mod.watched_names(mod.workflow_paths())
