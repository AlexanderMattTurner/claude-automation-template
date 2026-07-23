"""Tests for .github/scripts/script-configured.sh."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not available")


def write_package_json(repo: Path, scripts: dict[str, str]) -> None:
    (repo / "package.json").write_text(json.dumps({"name": "x", "scripts": scripts}))


def run_script(repo: Path, copy_script, name: str) -> subprocess.CompletedProcess:
    script = copy_script("script-configured.sh", repo)
    return subprocess.run(
        ["bash", str(script), name], cwd=repo, capture_output=True, text=True
    )


def test_exit_zero_when_script_configured(tmp_path: Path, copy_script) -> None:
    write_package_json(tmp_path, {"test": "vitest run"})
    result = run_script(tmp_path, copy_script, "test")
    assert result.returncode == 0, result.stderr


def test_exit_nonzero_for_placeholder_script(tmp_path: Path, copy_script) -> None:
    write_package_json(
        tmp_path, {"test": "echo 'ERROR: Configure test script' && exit 1"}
    )
    result = run_script(tmp_path, copy_script, "test")
    assert result.returncode != 0


def test_exit_nonzero_when_script_missing(tmp_path: Path, copy_script) -> None:
    write_package_json(tmp_path, {"build": "tsc"})
    result = run_script(tmp_path, copy_script, "test")
    assert result.returncode != 0


def test_errors_when_no_argument(tmp_path: Path, copy_script) -> None:
    script = copy_script("script-configured.sh", tmp_path)
    result = subprocess.run(
        ["bash", str(script)], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0


def test_malformed_package_json_fails_loud(tmp_path: Path, copy_script) -> None:
    """A corrupt package.json must NOT be conflated with "not configured".

    Exit >= 2 (distinct from the exit-1 "not configured") with a loud stderr,
    so a downstream `if:` gate can't skip the step and let a required check
    report green with zero work. Red on the old script, which used
    `jq -re … || exit 1` and returned 1 (== "not configured") on invalid JSON.
    """
    (tmp_path / "package.json").write_text('{ "scripts": { "test": }')  # invalid
    result = run_script(tmp_path, copy_script, "test")
    assert result.returncode >= 2, (result.returncode, result.stderr)
    assert "not valid JSON" in result.stderr


def run_output(
    repo: Path, copy_script, name: str, out_var: str = "configured"
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the workflow wrapper with a real $GITHUB_OUTPUT file; return the
    process and the file's contents."""
    copy_script("script-configured.sh", repo)  # sibling the wrapper resolves
    wrapper = copy_script("script-configured-output.sh", repo)
    gh_out = repo / "gh_output"
    gh_out.write_text("")
    result = subprocess.run(
        ["bash", str(wrapper), name, out_var],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "GITHUB_OUTPUT": str(gh_out)},
    )
    return result, gh_out.read_text()


def test_wrapper_writes_true_when_configured(tmp_path: Path, copy_script) -> None:
    write_package_json(tmp_path, {"test": "vitest run"})
    result, out = run_output(tmp_path, copy_script, "test")
    assert result.returncode == 0, result.stderr
    assert "configured=true" in out


def test_wrapper_writes_false_when_missing(tmp_path: Path, copy_script) -> None:
    write_package_json(tmp_path, {"build": "tsc"})
    result, out = run_output(tmp_path, copy_script, "test")
    assert result.returncode == 0, result.stderr
    assert "configured=false" in out


def test_wrapper_fails_loud_on_malformed_json(tmp_path: Path, copy_script) -> None:
    """The single decision point must fail the STEP on a malformed package.json
    (never emit configured=false), so the required check can't go green with
    zero work. Red on the old inline `if bash …; then true; else false; fi`,
    which routed exit >=2 into the false branch."""
    (tmp_path / "package.json").write_text('{ "scripts": { "test": }')
    result, out = run_output(tmp_path, copy_script, "test")
    assert result.returncode >= 2, (result.returncode, result.stderr)
    assert "configured=false" not in out
    assert "malformed" in result.stderr
