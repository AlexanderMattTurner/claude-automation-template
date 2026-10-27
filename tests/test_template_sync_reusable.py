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
import subprocess
import sys

import pytest
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


def _script_refs() -> list[tuple[str, str, str]]:
    """(job id, step name, prefix) for every `.github/scripts/` path a step runs."""
    refs = []
    for job_id, job in _load(REUSABLE)["jobs"].items():
        for step in job["steps"]:
            for match in SCRIPT_REF.finditer(step.get("run", "")):
                refs.append(
                    (job_id, step.get("name", step.get("id", "?")), match["prefix"])
                )
    return refs


# The sync job's resolve step lets a model edit the working tree, and later steps
# hold the PAT, so the sync job runs only the read-only copy staged outside it.
EXPECTED_PREFIX = {"preflight": "_template/", "sync": '"$RUNNER_TEMP/template-tools/'}


def test_every_script_runs_from_the_template_tooling():
    refs = _script_refs()
    # Non-vacuity: every step of the sync runs a script, so an empty or short
    # list means this test stopped seeing the steps, not that they are clean.
    assert len([r for r in refs if r[0] == "sync"]) >= 10, refs
    assert [r for r in refs if r[0] == "preflight"], refs
    assert [r for r in refs if r[2] != EXPECTED_PREFIX[r[0]]] == []


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


STALE_ENV = REPO_ROOT / ".github" / "scripts" / "template-sync-stale-env.py"


@pytest.mark.parametrize(
    ("caller_yaml", "moved"),
    [
        (
            'env:\n  EXCLUDE_PATHS: ".github/scripts/lib"\n'
            '  OPT_IN_PATHS: ".github/workflows/auto-version.yaml"\njobs: {}\n',
            [
                'exclude-paths: ".github/scripts/lib"',
                'opt-in-paths: ".github/workflows/auto-version.yaml"',
            ],
        ),
        ("env:\n  TEMPLATE_REPO: x/y\njobs: {}\n", []),
        (None, []),
    ],
    ids=["stale-lists", "clean-env", "no-caller-file"],
)
def test_a_caller_that_kept_its_env_path_lists_stops_the_sync(
    tmp_path, caller_yaml, moved
):
    """A caller's `env:` never reaches the called workflow, so a pre-reusable
    `EXCLUDE_PATHS` line would be ignored and its frozen paths synced over."""
    caller = tmp_path / "template-sync.yaml"
    if caller_yaml is not None:
        caller.write_text(caller_yaml, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(STALE_ENV), str(caller)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if moved else 0), result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if "::error" in line]
    assert len(errors) == len(moved)
    for line, entry in zip(errors, moved):
        assert entry in line


def test_the_stale_env_check_runs_before_the_sync():
    steps = _load(REUSABLE)["jobs"]["sync"]["steps"]
    runs = [step.get("run", "") for step in steps]
    check = next(i for i, run in enumerate(runs) if STALE_ENV.name in run)
    sync = next(i for i, step in enumerate(steps) if step.get("id") == "sync")
    assert check < sync
