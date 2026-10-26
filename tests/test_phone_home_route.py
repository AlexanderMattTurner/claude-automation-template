"""phone-home.yaml — file the lessons issue only from a repo that can.

A consumer with no TEMPLATE_SYNC_TOKEN cannot open an issue on the template, and
phone-home-submit.js throws on that failure. Ungated, every merged PR with a
lessons section in such a repo turns red. The route step decides first, and its
skip prints a warning so the lost lessons stay visible.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

from tests._helpers import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "phone-home.yaml"


def _steps() -> dict[str, dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["phone-home"]["steps"]
    return {step.get("id") or step.get("name") or step["uses"]: step for step in steps}


def _route(tmp_path: Path, *, has_token: str, is_template: str) -> tuple[str, str]:
    output = tmp_path / "out"
    output.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", _steps()["route"]["run"]],
        env={
            "PATH": "/usr/bin:/bin",
            "GITHUB_OUTPUT": str(output),
            "HAS_SYNC_TOKEN": has_token,
            "IS_TEMPLATE": is_template,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return output.read_text(encoding="utf-8"), result.stdout


@pytest.mark.parametrize(
    ("has_token", "is_template", "submit"),
    [
        ("true", "false", "true"),
        ("false", "true", "true"),
        ("true", "true", "true"),
        ("false", "false", "false"),
    ],
)
def test_route_submits_only_with_a_token_or_from_the_template(
    tmp_path: Path, has_token: str, is_template: str, submit: str
) -> None:
    output, stdout = _route(tmp_path, has_token=has_token, is_template=is_template)
    assert output == f"submit={submit}\n"
    assert ("::warning" in stdout) == (submit == "false")


def test_submit_step_runs_only_on_the_route_verdict() -> None:
    steps = _steps()
    assert steps["route"]["env"] == {
        "HAS_SYNC_TOKEN": "${{ secrets.TEMPLATE_SYNC_TOKEN != '' }}",
        "IS_TEMPLATE": "${{ github.repository == env.TEMPLATE_REPO }}",
    }
    assert (
        steps["Submit to template repo"]["if"] == "steps.route.outputs.submit == 'true'"
    )
