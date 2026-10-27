"""The shipped sync caller and the reusable workflow it calls must fit together.

A consumer runs `template-sync.yaml`, which calls the template's
`template-sync-reusable.yaml` at a moving ref. The point of that split is that a
consumer always runs the TEMPLATE's sync scripts, so a stale local copy cannot
break the sync that would replace it. Three mistakes undo it, and none of them
fails until the cron fires in a consumer:

- a step that runs `.github/scripts/<x>` from the caller's tree, which is the
  stale copy again;
- a caller whose `permissions:` grant less than a called job requests, which
  ends the run in `startup_failure` before any job starts;
- a secret the reusable reads but does not declare, which arrives empty.
"""

import re

import yaml

from tests._helpers import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CALLER = WORKFLOWS / "template-sync.yaml"
REUSABLE = WORKFLOWS / "template-sync-reusable.yaml"
ON_KEY = True  # YAML 1.1 reads the unquoted `on` key as the boolean True.
SCRIPT_REF = re.compile(r"(?P<prefix>\S*?)\.github/scripts/[\w.-]+")
LEVEL = {"none": 0, "read": 1, "write": 2}


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _script_refs() -> list[tuple[str, str]]:
    """(step name, prefix) for every `.github/scripts/` path a step runs."""
    refs = []
    for job in _load(REUSABLE)["jobs"].values():
        for step in job["steps"]:
            for match in SCRIPT_REF.finditer(step.get("run", "")):
                refs.append((step.get("name", step.get("id", "?")), match["prefix"]))
    return refs


def test_every_script_runs_from_the_template_checkout():
    refs = _script_refs()
    # Non-vacuity: every step of the sync runs a script, so an empty or short
    # list means this test stopped seeing the steps, not that they are clean.
    assert len(refs) >= 10, refs
    assert [r for r in refs if r[1] != "_template/"] == []


def test_the_caller_grants_every_permission_a_called_job_requests():
    granted = _load(CALLER)["jobs"]["sync"]["permissions"]
    for name, job in _load(REUSABLE)["jobs"].items():
        for scope, level in job["permissions"].items():
            held = granted.get(scope, "none")
            assert LEVEL[held] >= LEVEL[level], (
                f"job {name} requests {scope}: {level}, the caller grants {held}"
            )


def test_every_secret_the_reusable_reads_is_declared():
    text = REUSABLE.read_text(encoding="utf-8")
    read = set(re.findall(r"secrets\.(?P<name>[A-Z0-9_]+)", text)) - {"GITHUB_TOKEN"}
    declared = set(_load(REUSABLE)[ON_KEY]["workflow_call"]["secrets"])
    assert "TEMPLATE_SYNC_TOKEN_ORG" in read
    assert read == declared


def test_the_caller_names_a_template_ref_and_passes_its_secrets():
    job = _load(CALLER)["jobs"]["sync"]
    assert job["uses"].startswith(
        "AlexanderMattTurner/claude-automation-template/"
        ".github/workflows/template-sync-reusable.yaml@"
    )
    assert job["with"]["template-ref"]
    assert job["secrets"] == "inherit"
