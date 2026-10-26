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
    list_should_fail: bool = False,
    labels_should_fail: bool = False,
    env_overrides: dict | None = None,
) -> tuple[dict, subprocess.CompletedProcess]:
    """Invoke phone-home-submit.js with a mock github-script environment.

    Records every github.rest.issues.create/listForRepo/addLabels call into a
    JSON file so tests can assert on what the real script did, rather than
    re-implementing its dedup/create logic."""
    PHONE_HOME_DIR.mkdir(parents=True, exist_ok=True)
    (PHONE_HOME_DIR / "lessons.txt").write_text(lessons, encoding="utf-8")

    wrapper = tmp_path / "run.js"
    calls_file = tmp_path / "calls.json"
    wrapper.write_text(
        f"""
const fs = require("fs");
const submit = require({json.dumps(str(SCRIPT))});
const calls = {{ create: [], listForRepo: [], addLabels: [] }};
const existingIssues = {json.dumps(existing_issues or [])};
const createShouldFail = {str(create_should_fail).lower()};
const listShouldFail = {str(list_should_fail).lower()};
const labelsShouldFail = {str(labels_should_fail).lower()};

const github = {{
  paginate: async (fn, params) => {{
    const res = await fn(params);
    return res.data;
  }},
  rest: {{
    issues: {{
      listForRepo: async (params) => {{
        calls.listForRepo.push(params);
        if (listShouldFail) {{
          throw new Error("simulated list failure");
        }}
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
        if (labelsShouldFail) {{
          throw new Error("simulated addLabels failure");
        }}
      }},
    }},
  }},
}};

submit({{ github }})
  .then(() => {{
    fs.writeFileSync(process.env.CALLS_FILE, JSON.stringify(calls));
  }})
  .catch((err) => {{
    fs.writeFileSync(process.env.CALLS_FILE, JSON.stringify(calls));
    process.stderr.write(err.message + "\\n");
    process.exit(1);
  }});
""",
        encoding="utf-8",
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
    if calls_file.exists():
        calls = json.loads(calls_file.read_text(encoding="utf-8"))
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


def test_create_failure_fails_the_run(tmp_path: Path) -> None:
    """A create failure (e.g. a missing TEMPLATE_SYNC_TOKEN) must go red. The
    run fires once per merged PR, so a green run here loses its lessons."""
    calls, result = run_submit(tmp_path, create_should_fail=True)
    assert result.returncode != 0
    assert "simulated create failure" in result.stderr
    assert len(calls["create"]) == 1
    assert calls["addLabels"] == []


def test_dedup_failure_fails_the_run_before_creating(tmp_path: Path) -> None:
    """A dedup that cannot read the open issues must not create blind: a re-run
    after a later failure would then file the same lessons twice."""
    calls, result = run_submit(tmp_path, list_should_fail=True)
    assert result.returncode != 0
    assert "simulated list failure" in result.stderr
    assert calls["create"] == []


def test_label_failure_fails_the_run(tmp_path: Path) -> None:
    """The dedup lists issues by the phone-home label, so an unlabelled issue is
    invisible to the next re-run's dedup and gets filed again."""
    calls, result = run_submit(tmp_path, labels_should_fail=True)
    assert result.returncode != 0
    assert "simulated addLabels failure" in result.stderr
    assert len(calls["create"]) == 1
