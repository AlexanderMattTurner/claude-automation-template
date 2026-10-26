"""Tests for .claude/hooks/lib-checks.sh has_script, which asks
.github/scripts/script-configured.sh.

The load-bearing guard: a malformed package.json (or a missing jq) must fail
LOUD with exit 2 — the "could not classify" contract — never be silently
misread as "script not configured" (which would skip real pre-push checks).
"""

import shutil
import subprocess
from pathlib import Path


from tests._helpers import REPO_ROOT

LIB = REPO_ROOT / ".claude" / "hooks" / "lib-checks.sh"


def _run_has_script(project_dir: Path, name: str = "build", path: str | None = None):
    env = {"CLAUDE_PROJECT_DIR": str(project_dir)}
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["bash", "-c", f'source "{LIB}"; has_script "{name}"'],
        env={**env},
        capture_output=True,
        text=True,
    )


def test_malformed_package_json_exits_2(tmp_path: Path) -> None:
    """RED without the guard: a broken package.json would fall through to
    'not configured' (exit 1) instead of the loud exit 2."""
    (tmp_path / "package.json").write_text("{ not valid json ", encoding="utf-8")
    # Provide a real PATH so jq is found and actually reports the parse error.
    import os

    result = _run_has_script(tmp_path, path=os.environ["PATH"])
    assert result.returncode == 2, result.stderr
    assert "cannot read package.json" in result.stderr


def test_configured_script_returns_0(tmp_path: Path) -> None:
    import os

    (tmp_path / "package.json").write_text(
        '{"scripts": {"build": "tsc"}}', encoding="utf-8"
    )
    result = _run_has_script(tmp_path, path=os.environ["PATH"])
    assert result.returncode == 0, result.stderr


def test_unconfigured_script_returns_1_not_2(tmp_path: Path) -> None:
    """A well-formed manifest that simply lacks the script is 'not configured'
    (exit 1) — distinct from the exit-2 'could not classify'."""
    import os

    (tmp_path / "package.json").write_text(
        '{"scripts": {"lint": "eslint"}}', encoding="utf-8"
    )
    result = _run_has_script(tmp_path, path=os.environ["PATH"])
    assert result.returncode == 1


def test_missing_jq_fails_loud(tmp_path: Path) -> None:
    """With a package.json present but jq absent from PATH, has_script must not
    silently return 'not configured' — jq's absence trips the loud exit 2."""
    (tmp_path / "package.json").write_text(
        '{"scripts": {"build": "tsc"}}', encoding="utf-8"
    )
    # A PATH containing only bash (so the subprocess can start) but no jq.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to(shutil.which("bash"))
    result = _run_has_script(tmp_path, path=str(bin_dir))
    assert result.returncode == 2, result.stderr


def test_a_missing_predicate_fails_loud(tmp_path: Path) -> None:
    """lib-checks.sh holds no copy of the predicate, so without
    script-configured.sh beside it there is no answer: exit 2, never 1."""
    lib = tmp_path / ".claude" / "hooks" / "lib-checks.sh"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(LIB.read_bytes())
    (tmp_path / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", f'source "{lib}"; has_script build'],
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "script-configured.sh, which is missing" in result.stderr
