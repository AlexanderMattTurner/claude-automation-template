"""template-sync's `dry-run` input must be compared as a boolean.

The input is declared `type: boolean`, so GitHub hands the expression a real
boolean. Comparing it to the STRING 'true' never matches: GitHub casts across
types to a number, so `true == 'true'` compares 1 with NaN. The dry-run branch
was therefore dead and the pull-request branch always ran — every "dry run"
dispatch opened a real pull request, with no error and nothing in the log to say
so.

That is invisible from inside the workflow, which is why it survived long enough
to reach every downstream repo. These tests read the workflows GitHub actually
executes and pin the type agreement between each declaration and its readers:
the shipped caller declares the dispatch input and forwards it, and the reusable
sync workflow declares it again and reads it in its step conditions.
"""

import re

import pytest
import yaml

from tests._helpers import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CALLER = WORKFLOWS / "template-sync.yaml"
REUSABLE = WORKFLOWS / "template-sync-reusable.yaml"
INPUT_NAME = "dry-run"

# `on` is the YAML 1.1 boolean True, which is what safe_load gives back for the
# unquoted key. Reading it as the string "on" finds nothing and the whole suite
# passes vacuously.
ON_KEY = True


def _workflow(path=REUSABLE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


DECLARATIONS = pytest.mark.parametrize(
    ("path", "trigger"),
    [(CALLER, "workflow_dispatch"), (REUSABLE, "workflow_call")],
    ids=["caller", "reusable"],
)


def _conditions() -> list[str]:
    """Every step `if:` in the workflow that reads the dry-run input."""
    found = []
    for job in _workflow()["jobs"].values():
        for step in job.get("steps", []):
            condition = step.get("if")
            if isinstance(condition, str) and f"inputs.{INPUT_NAME}" in condition:
                found.append(condition)
    return found


@DECLARATIONS
def test_the_input_is_declared_boolean(path, trigger):
    spec = _workflow(path)[ON_KEY][trigger]["inputs"][INPUT_NAME]
    assert spec["type"] == "boolean"


@DECLARATIONS
def test_the_default_is_a_boolean_not_a_string(path, trigger):
    # A quoted "false" default is the same type confusion one step earlier: it
    # reads as truthy wherever a caller forwards it verbatim.
    spec = _workflow(path)[ON_KEY][trigger]["inputs"][INPUT_NAME]
    assert spec["default"] is False, f"default is {spec['default']!r}, not a boolean"


def test_the_caller_forwards_a_boolean():
    # A scheduled run carries no inputs, so a bare `inputs.dry-run` forwards
    # null. The comparison yields a real boolean on both triggers.
    forwarded = _workflow(CALLER)["jobs"]["sync"]["with"][INPUT_NAME]
    assert forwarded == "${{ inputs.dry-run == true }}"


def test_every_condition_compares_the_input_to_a_boolean():
    conditions = _conditions()
    # Non-vacuity: the assertion below is satisfied by an empty list, and this
    # workflow's whole dry-run behaviour is carried by these conditions.
    assert len(conditions) >= 5, (
        f"expected the dry-run conditions to still be here, found {len(conditions)}"
    )
    quoted = [
        c
        for c in conditions
        if re.search(rf"inputs\.{re.escape(INPUT_NAME)}\s*[!=]=\s*'", c)
    ]
    assert quoted == [], (
        "these conditions compare a boolean input to a string, which never "
        f"matches: {quoted}"
    )


def test_the_resolver_steps_run_only_when_a_conflict_carries_markers():
    # A kept-local conflict sets has_conflicts but emits no conflict_files, and
    # template-sync-resolve.sh refuses an empty list, so gating these steps on
    # has_conflicts turned every kept-local-only sync red.
    steps = [
        step
        for job in _workflow()["jobs"].values()
        for step in job.get("steps", [])
        if step.get("id") == "resolve"
        or str(step.get("name", "")).startswith("Install mergiraf")
    ]
    assert len(steps) == 2, f"found {len(steps)} resolver steps, expected 2"
    for step in steps:
        assert "steps.sync.outputs.conflict_files != ''" in step["if"], step["name"]
        assert "has_conflicts" not in step["if"], step["name"]
