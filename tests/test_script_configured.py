"""Tests for .github/scripts/script-configured.sh."""

import json
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


def test_exit_one_when_no_package_json(tmp_path: Path, copy_script) -> None:
    """No package.json at all is a legitimate "not configured", not an error."""
    result = run_script(tmp_path, copy_script, "test")
    assert result.returncode == 1


def test_malformed_package_json_fails_loud(tmp_path: Path, copy_script) -> None:
    """Malformed package.json must exit >=2 with a diagnostic — treating it as
    "not configured" (exit 1) would green a required check with zero checks run."""
    (tmp_path / "package.json").write_text('{"scripts": {')
    result = run_script(tmp_path, copy_script, "test")
    assert result.returncode >= 2
    assert "cannot read package.json" in result.stderr


def run_decide(repo: Path, copy_script, *names: str) -> subprocess.CompletedProcess:
    copy_script("script-configured.sh", repo)
    decide = copy_script("decide-script-configured.sh", repo)
    return subprocess.run(
        ["bash", str(decide), *names], cwd=repo, capture_output=True, text=True
    )


def test_decide_emits_true_and_false_outputs(tmp_path: Path, copy_script) -> None:
    write_package_json(
        tmp_path,
        {"test": "vitest run", "lint": "echo 'ERROR: Configure lint' && exit 1"},
    )
    result = run_decide(tmp_path, copy_script, "test", "lint")
    assert result.returncode == 0, result.stderr
    assert "test_configured=true" in result.stdout
    assert "lint_configured=false" in result.stdout


def test_decide_fails_loud_on_malformed_package_json(
    tmp_path: Path, copy_script
) -> None:
    """The decide helper must propagate the loud >=2 failure, so the CI step
    goes red instead of writing <name>_configured=false and skipping-to-green."""
    (tmp_path / "package.json").write_text("not json at all")
    result = run_decide(tmp_path, copy_script, "test")
    assert result.returncode >= 2
    assert "cannot read package.json" in result.stderr
    assert "test_configured" not in result.stdout


def test_decide_requires_at_least_one_name(tmp_path: Path, copy_script) -> None:
    result = run_decide(tmp_path, copy_script)
    assert result.returncode == 2
    assert "usage" in result.stderr
