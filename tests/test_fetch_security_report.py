"""Tests for .github/scripts/fetch-security-report.sh.

The load-bearing guard: a FAILED fetch (permissions/transient API error) must
report "could not fetch", never render as a clean "no alerts" — otherwise a
broken token would produce a falsely-reassuring empty security report.
"""

import os
import stat
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "fetch-security-report.sh"


def _write_gh(bin_dir: Path, body: str) -> None:
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\n" + body)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path: Path, gh_body: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_gh(bin_dir, gh_body)
    report = tmp_path / "report.md"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_TOKEN": "x",
        "REPO": "owner/repo",
        "REPORT_PATH": str(report),
        "GITHUB_ENV": str(tmp_path / "env.txt"),
    }
    # cwd without package.json so the pnpm-audit branch is skipped cleanly.
    subprocess.run(
        ["bash", str(SCRIPT)], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    return report.read_text()


def test_failed_socket_fetch_reports_error_not_no_alerts(tmp_path: Path) -> None:
    """RED without the exit-code branch: a failed PR-list fetch would yield an
    empty list that reads as a clean 'no alerts found'."""
    # Every gh call fails (nonzero exit).
    report = _run(tmp_path, "exit 1\n")
    assert "Could not fetch open PRs for Socket.dev scan" in report
    assert "No Socket.dev alerts found" not in report


def test_successful_empty_scan_reports_no_alerts(tmp_path: Path) -> None:
    """Contrast: when the fetch SUCCEEDS and returns nothing, 'no alerts' is the
    correct, honest message — the guard must not cry wolf on a clean success."""
    # gh succeeds, returning empty output for every call.
    report = _run(tmp_path, "exit 0\n")
    assert "No Socket.dev alerts found in recent open PRs." in report
    assert "Could not fetch open PRs for Socket.dev scan" not in report
