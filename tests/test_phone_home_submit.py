"""Tests for .github/scripts/phone-home-submit.js."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None and not os.environ.get("CI"),
    reason="node not available",
)

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)
SCRIPT = REPO_ROOT / ".github" / "scripts" / "phone-home-submit.js"
# The script hardcodes this input dir, so these tests must run serially
# (no pytest-xdist); the autouse fixture clears it before/after each test.
PHONE_HOME_DIR = Path("/tmp/phone-home")


def run_submit(
    tmp_path: Path,
    *,
    lessons: str = "- A lesson.\n",
    existing_issues: list | None = None,
    create_should_fail: bool = False,
    env_overrides: dict | None = None,
) -> tuple[dict, subprocess.CompletedProcess]:
    """Invoke phone-home-submit.js with a mock github-script environment.

    Records every github.rest.issues.create/listForRepo/addLabels call into a
    JSON file so tests can assert on what the real script did, rather than
    re-implementing its dedup/create logic."""
    PHONE_HOME_DIR.mkdir(parents=True, exist_ok=True)
    (PHONE_HOME_DIR / "lessons.txt").write_text(lessons)

    wrapper = tmp_path / "run.js"
    calls_file = tmp_path / "calls.json"
    wrapper.write_text(
        f"""
const fs = require("fs");
const submit = require({json.dumps(str(SCRIPT))});
const calls = {{ create: [], listForRepo: [], addLabels: [] }};
const existingIssues = {json.dumps(existing_issues or [])};
const createShouldFail = {str(create_should_fail).lower()};

const github = {{
  paginate: async (fn, params) => {{
    const res = await fn(params);
    return res.data;
  }},
  rest: {{
    issues: {{
      listForRepo: async (params) => {{
        calls.listForRepo.push(params);
        return {{ data: existingIssues }};
      }},
      create: async (params) => {{
        calls.create.push(params);
        if (createShouldFail) {{
          throw new Error("simulated create failure");
        }}
        return {{
          data: {{
            html_url: "https://github.com/tmpl/repo/issues/99",
            number: 99,
          }},
        }};
      }},
      addLabels: async (params) => {{
        calls.addLabels.push(params);
      }},
    }},
  }},
}};

submit({{ github }})
  .then(() => {{
    fs.writeFileSync(process.env.CALLS_FILE, JSON.stringify(calls));
  }})
  .catch((err) => {{
    process.stderr.write(err.message + "\\n");
    process.exit(1);
  }});
"""
    )
    env = {
        **os.environ,
        "PR_TITLE": "Test PR",
        "PR_URL": "https://github.com/owner/repo/pull/1",
        "SOURCE_REPO": "owner/repo",
        "TEMPLATE_REPO": "tmpl/repo",
        "CALLS_FILE": str(calls_file),
    }
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["node", str(wrapper)], env=env, capture_output=True, text=True
    )
    calls: dict = {}
    if result.returncode == 0 and calls_file.exists():
        calls = json.loads(calls_file.read_text())
    return calls, result


@pytest.fixture(autouse=True)
def clean_phone_home_dir():
    """Remove any stale lessons.txt before/after each test."""
    lessons = PHONE_HOME_DIR / "lessons.txt"
    lessons.unlink(missing_ok=True)
    yield
    lessons.unlink(missing_ok=True)


def test_creates_issue_when_none_exists(tmp_path: Path) -> None:
    calls, result = run_submit(tmp_path)
    assert result.returncode == 0, result.stderr
    assert len(calls["create"]) == 1
    assert calls["create"][0]["title"] == "[phone-home] Test PR"
    assert len(calls["addLabels"]) == 1


def test_skips_create_when_issue_already_exists(tmp_path: Path) -> None:
    """A job re-run (e.g. after a transient createLabel/addLabels failure in a
    prior attempt) must not create a second, duplicate issue for the same
    source PR."""
    calls, result = run_submit(
        tmp_path,
        existing_issues=[
            {
                "title": "[phone-home] Test PR",
                "html_url": "https://github.com/tmpl/repo/issues/42",
            }
        ],
    )
    assert result.returncode == 0, result.stderr
    assert calls["create"] == []
    assert calls["addLabels"] == []


def test_ignores_pull_requests_with_matching_title(tmp_path: Path) -> None:
    """listForRepo also returns pull requests; a same-titled PR must not
    suppress issue creation (only real issues are dedup targets)."""
    calls, result = run_submit(
        tmp_path,
        existing_issues=[
            {
                "title": "[phone-home] Test PR",
                "pull_request": {},
                "html_url": "https://github.com/tmpl/repo/pull/7",
            }
        ],
    )
    assert result.returncode == 0, result.stderr
    assert len(calls["create"]) == 1


def test_missing_env_vars_raises(tmp_path: Path) -> None:
    calls, result = run_submit(tmp_path, env_overrides={"PR_TITLE": ""})
    assert result.returncode != 0
    assert "Missing required env vars" in result.stderr


def test_create_failure_does_not_raise(tmp_path: Path) -> None:
    """A create failure (e.g. missing TEMPLATE_SYNC_TOKEN) must degrade
    gracefully, matching the workflow's tolerance for unconfigured phone-home."""
    calls, result = run_submit(tmp_path, create_should_fail=True)
    assert result.returncode == 0, result.stderr
    assert len(calls["create"]) == 1
    assert calls["addLabels"] == []
