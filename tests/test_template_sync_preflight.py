"""A repository must never sync itself from a copy of itself.

The template ships `template-sync.yaml` to its consumers, so the workflow also
runs in the template repo — where there is no upstream to import from. Left
unguarded that is not a harmless no-op: pointed at a fork of itself (what a
stale `TEMPLATE_SYNC_ORG` variable produces), the sync rewrites the source of
truth from a drifted copy and opens a pull request saying so, which is exactly
what happened on PR #430.

Both directions are tested, because the guard has two failure modes and only one
of them is visible: refusing a legitimate downstream sync stops every consumer
from receiving updates, and it stops them silently — the workflow is a cron with
no pull request to notice.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "template-sync-preflight.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "template-sync.yaml"


def run(tmp_path: Path, template_repo: str, running_repo: str, parent: str = ""):
    """Run the guard with a fake `gh` whose fork-parent answer is `parent`; a
    `parent` of "!fail" makes the lookup fail the way a private template does."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    body = (
        "echo 'gh: Not Found' >&2; exit 1\n"
        if parent == "!fail"
        else f"printf '%s\\n' '{parent}'\n"
    )
    (bin_dir / "gh").write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)

    output = tmp_path / "gh-output"
    output.touch()
    res = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin",
            "TEMPLATE_REPO": template_repo,
            "GITHUB_REPOSITORY": running_repo,
            "GITHUB_OUTPUT": str(output),
            "GH_TOKEN": "x",
        },
    )
    return res, output.read_text(encoding="utf-8")


TEMPLATE = "AlexanderMattTurner/claude-automation-template"


def test_refuses_when_the_source_is_this_repo(tmp_path):
    res, out = run(tmp_path, TEMPLATE, TEMPLATE)
    assert "self_sync=true" in out
    # Green: the template running its own shipped workflow is not an error.
    assert res.returncode == 0, res.stderr


def test_slug_comparison_ignores_case(tmp_path):
    # GitHub slugs are case-insensitive, so a re-cased owner is the same repo.
    res, out = run(tmp_path, TEMPLATE.lower(), TEMPLATE)
    assert "self_sync=true" in out
    assert res.returncode == 0, res.stderr


def test_refuses_and_fails_when_the_source_is_a_fork_of_this_repo(tmp_path):
    res, out = run(
        tmp_path, "someone-else/claude-automation-template", TEMPLATE, parent=TEMPLATE
    )
    assert "self_sync=true" in out
    # Loud: a misconfigured variable needs a human, and a silent weekly skip
    # reads exactly like a healthy weekly run.
    assert res.returncode == 1
    assert "fork of this repository" in res.stderr


def test_allows_a_downstream_repo(tmp_path):
    res, out = run(tmp_path, TEMPLATE, "someone/their-project")
    assert "self_sync=false" in out
    assert res.returncode == 0, res.stderr


def test_allows_a_fork_syncing_from_the_upstream_template(tmp_path):
    # The fork's parent is the template, not the running repo — that is the
    # normal direction and must keep working.
    res, out = run(
        tmp_path,
        TEMPLATE,
        "someone/claude-automation-template",
        parent="someone/upstream",
    )
    assert "self_sync=false" in out
    assert res.returncode == 0, res.stderr


def test_proceeds_when_the_fork_parent_cannot_be_read(tmp_path):
    res, out = run(tmp_path, TEMPLATE, "someone/their-project", parent="!fail")
    assert "self_sync=false" in out
    assert res.returncode == 0, res.stderr
    assert "::warning::" in res.stdout


@pytest.mark.parametrize("missing", ["TEMPLATE_REPO", "GITHUB_REPOSITORY"])
def test_requires_its_inputs(tmp_path, missing):
    env = {
        "PATH": "/usr/bin:/bin",
        "TEMPLATE_REPO": TEMPLATE,
        "GITHUB_REPOSITORY": TEMPLATE,
        "GITHUB_OUTPUT": str(tmp_path / "out"),
    }
    del env[missing]
    res = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
    assert res.returncode != 0
    assert missing in res.stderr


def test_the_sync_job_is_gated_on_the_guard():
    # Without this the script above can hold every opinion it likes while the
    # sync job runs anyway.
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    assert "preflight" in jobs["sync"]["needs"]
    assert jobs["sync"]["if"] == "needs.preflight.outputs.self_sync != 'true'"
    steps = jobs["preflight"]["steps"]
    assert any(SCRIPT.name in step.get("run", "") for step in steps)
