"""Tests for .github/scripts/paths-changed.sh — the CI `decide` job helper."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PATHS_CHANGED = REPO_ROOT / ".github" / "scripts" / "paths-changed.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one base commit containing a known file tree."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const a = 1;\n")
    (tmp_path / "README.md").write_text("# base\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _run(repo: Path, *pathspecs: str, base: str | None, head: str) -> tuple[int, str]:
    """Run paths-changed.sh; return (returncode, run-value) where run-value is
    'true', 'false', or '' if nothing was written."""
    out_file = repo / "gh_output"
    out_file.touch()
    env = {
        "GITHUB_OUTPUT": str(out_file),
        "HEAD_SHA": head,
        "PATH": "/usr/bin:/bin",
    }
    if base is not None:
        env["BASE_SHA"] = base
    result = subprocess.run(
        ["bash", str(PATHS_CHANGED), *pathspecs],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    run_lines = [
        line.split("=", 1)[1]
        for line in out_file.read_text().splitlines()
        if line.startswith("run=")
    ]
    return result.returncode, (run_lines[-1] if run_lines else "")


def test_matching_change_runs(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "app.ts").write_text("export const a = 2;\n")
    _git(repo, "commit", "-aqm", "change ts")
    head = _git(repo, "rev-parse", "HEAD")
    rc, run = _run(repo, "*.ts", base=base, head=head)
    assert rc == 0
    assert run == "true"


def test_nonmatching_change_skips(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("# changed\n")
    _git(repo, "commit", "-aqm", "docs only")
    head = _git(repo, "rev-parse", "HEAD")
    rc, run = _run(repo, "*.ts", "package.json", base=base, head=head)
    assert rc == 0
    assert run == "false"


def test_directory_pathspec_matches_nested_file(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    nested = repo / "src" / "deep" / "x.ts"
    nested.parent.mkdir()
    nested.write_text("export const x = 1;\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "nested")
    head = _git(repo, "rev-parse", "HEAD")
    rc, run = _run(repo, "src", base=base, head=head)
    assert rc == 0
    assert run == "true"


@pytest.mark.parametrize(
    "base",
    [
        "",
        "0000000000000000000000000000000000000000",
        "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    ],
    ids=["empty", "all-zero", "unreachable"],
)
def test_fails_open_when_base_undeterminable(repo: Path, base: str) -> None:
    """Missing/zero/unreachable base must run the job, never silently skip it."""
    head = _git(repo, "rev-parse", "HEAD")
    rc, run = _run(repo, "*.ts", base=base, head=head)
    assert rc == 0
    assert run == "true"


def test_no_pathspecs_is_an_error(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    rc, run = _run(repo, base=head, head=head)
    assert rc == 2
    assert run == ""
