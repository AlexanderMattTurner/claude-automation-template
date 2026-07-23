"""Tests for .github/scripts/release-canary.sh publish-applicability gating.

The canary must not fire a daily red alert on a repo that simply is not an npm
publisher. These tests stub `npm` on PATH so the E404 (never-published) path is
exercised without touching the real registry.
"""

import os
import stat
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "release-canary.sh"


def _fake_npm(bin_dir: Path) -> None:
    """Install an `npm` shim that reports every package as never-published
    (E404 JSON on stdout, exit 1), matching real `npm view --json` behavior."""
    npm = bin_dir / "npm"
    npm.write_text(
        "#!/usr/bin/env bash\n"
        'echo \'{"error":{"code":"E404","summary":"not found"}}\'\n'
        "exit 1\n"
    )
    npm.chmod(npm.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path: Path, package_json: str) -> subprocess.CompletedProcess:
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    bin_dir.mkdir()
    (repo / "package.json").write_text(package_json)
    _fake_npm(bin_dir)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True
    )


def test_never_published_no_publishconfig_is_not_applicable(tmp_path: Path) -> None:
    """A non-private package that was never published and declares no
    publishConfig is simply not an npm publisher: skip (exit 0) with guidance,
    NOT a daily red alarm. RED before the fix: the old code exited 1 here."""
    result = _run(tmp_path, '{"name": "some-tool", "version": "0.0.0"}')
    assert result.returncode == 0, result.stderr
    assert "not an npm publisher" in result.stderr
    assert "Disable this check" in result.stderr


def test_never_published_with_publishconfig_fails_loud(tmp_path: Path) -> None:
    """Declaring publishConfig is a promise to publish; if nothing was ever
    published, the pipeline is broken and the canary must go RED."""
    result = _run(
        tmp_path,
        '{"name": "some-tool", "version": "0.0.0",'
        ' "publishConfig": {"access": "public"}}',
    )
    assert result.returncode == 1
    assert "never been published" in result.stderr


def test_private_repo_still_skips(tmp_path: Path) -> None:
    """The pre-existing private-repo skip must survive the new gating."""
    result = _run(tmp_path, '{"name": "some-tool", "private": true}')
    assert result.returncode == 0, result.stderr
    assert "does not publish to npm" in result.stderr
