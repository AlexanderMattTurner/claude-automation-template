"""Tests for .github/scripts/template-sync.sh.

These tests stand up a tiny in-memory "template" git repo plus a "child" git
repo, run the sync script with controlled inputs, and assert on the resulting
file contents + GITHUB_OUTPUT entries.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent))
from conftest import GIT_IDENTITY_ENV, init_test_repo  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "template-sync.sh"


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, **GIT_IDENTITY_ENV}
    result = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    init_test_repo(path)
    return path


def commit(repo: Path, message: str = "x") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def parse_outputs(github_output: Path) -> dict[str, str]:
    """Parse a GITHUB_OUTPUT file. Supports both key=value and key<<EOF blocks."""
    text = github_output.read_text()
    result: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, sentinel = line.split("<<", 1)
            i += 1
            collected: list[str] = []
            while i < len(lines) and lines[i] != sentinel:
                collected.append(lines[i])
                i += 1
            result[key] = "\n".join(collected)
        elif "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
        i += 1
    return result


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """A clean child repo with a sibling template repo. Tests access them as
    `workdir / "child"` and `workdir / "template"`; run_sync() copies the
    template into `child/_template` so the script's relative paths line up."""
    init_repo(tmp_path / "child")
    init_repo(tmp_path / "template")
    return tmp_path


def run_sync(
    child: Path,
    template: Path,
    *,
    sync_paths: str,
    exclude_paths: str = "",
    work_dir: Path | None = None,
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run template-sync.sh against `child`, treating `template` as the source."""
    # Set up _template as a working copy of `template` inside `child`.
    template_copy = child / "_template"
    if template_copy.exists():
        subprocess.run(["rm", "-rf", str(template_copy)], check=True)
    subprocess.run(["cp", "-a", str(template), str(template_copy)], check=True)

    output_file = child.parent / f"github_output_{child.name}.txt"
    output_file.write_text("")

    work = work_dir or (child.parent / f"work_{child.name}")
    work.mkdir(exist_ok=True)

    env = {
        **os.environ,
        **GIT_IDENTITY_ENV,
        "SYNC_PATHS": sync_paths,
        "EXCLUDE_PATHS": exclude_paths,
        "GITHUB_OUTPUT": str(output_file),
        "TEMPLATE_SYNC_WORK_DIR": str(work),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=child, env=env, capture_output=True, text=True
    )
    return result, output_file, work


def test_adds_new_file_from_template(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"
    write(template / "config" / "hello.txt", "from template\n")
    commit(template)

    result, output_file, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    assert (child / "config" / "hello.txt").read_text() == "from template\n"
    outputs = parse_outputs(output_file)
    assert outputs["has_changes"] == "true"
    assert outputs["has_conflicts"] == "false"
    assert outputs["has_deletions"] == "false"


def test_no_changes_when_files_identical(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"
    write(template / "config" / "a.txt", "same\n")
    sha = commit(template)
    write(child / "config" / "a.txt", "same\n")
    # The sync script writes the SHA with a trailing newline; match it here so
    # rewriting the file doesn't itself count as a change.
    (child / ".template-version").write_text(f"{sha}\n")
    commit(child)

    result, output_file, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    outputs = parse_outputs(output_file)
    assert outputs["has_changes"] == "false"
    assert outputs["has_conflicts"] == "false"


def test_auto_merges_when_only_template_changed(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"

    write(template / "config" / "a.txt", "line1\nline2\nline3\n")
    prev_sha = commit(template)

    # Local matches the previous sync point exactly.
    write(child / "config" / "a.txt", "line1\nline2\nline3\n")
    (child / ".template-version").write_text(prev_sha)
    commit(child)

    # Template advances.
    write(template / "config" / "a.txt", "line1\nLINE2-CHANGED\nline3\n")
    commit(template)

    result, output_file, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    outputs = parse_outputs(output_file)
    assert outputs["has_conflicts"] == "false"
    assert (child / "config" / "a.txt").read_text() == "line1\nLINE2-CHANGED\nline3\n"


def test_keeps_local_when_only_local_changed(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"

    write(template / "config" / "a.txt", "shared\n")
    prev_sha = commit(template)

    # Local diverged from the previous template version; template is unchanged.
    write(child / "config" / "a.txt", "local-customized\n")
    (child / ".template-version").write_text(prev_sha)
    commit(child)

    # Template stays the same — advance with an unrelated commit.
    write(template / "other.txt", "noop\n")
    commit(template)

    result, output_file, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    outputs = parse_outputs(output_file)
    assert outputs["has_conflicts"] == "false"
    assert (child / "config" / "a.txt").read_text() == "local-customized\n"


def test_3way_merge_conflict_produces_markers(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"

    write(template / "config" / "a.txt", "shared line\n")
    prev_sha = commit(template)

    write(child / "config" / "a.txt", "LOCAL change\n")
    (child / ".template-version").write_text(prev_sha)
    commit(child)

    write(template / "config" / "a.txt", "TEMPLATE change\n")
    commit(template)

    result, output_file, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    body = (child / "config" / "a.txt").read_text()
    assert "<<<<<<<" in body
    assert ">>>>>>>" in body
    outputs = parse_outputs(output_file)
    assert outputs["has_conflicts"] == "true"
    assert "config/a.txt" in outputs["conflict_files"]
    assert (child / ".template-sync-conflicts").exists()


def test_no_base_conflict_when_local_differs_without_prev_sha(workdir: Path) -> None:
    """First-sync collision: file exists in both but no .template-version."""
    child = workdir / "child"
    template = workdir / "template"

    write(template / "config" / "a.txt", "template version\n")
    commit(template)
    write(child / "config" / "a.txt", "local version\n")
    commit(child)

    result, output_file, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    outputs = parse_outputs(output_file)
    assert outputs["has_conflicts"] == "true"
    assert (child / "config" / "a.txt").read_text() == "template version\n"


def test_detects_deleted_files(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"

    write(template / "config" / "a.txt", "x\n")
    write(template / "config" / "b.txt", "y\n")
    prev_sha = commit(template)

    write(child / "config" / "a.txt", "x\n")
    write(child / "config" / "b.txt", "y\n")
    (child / ".template-version").write_text(prev_sha)
    commit(child)

    # Delete b.txt in template.
    (template / "config" / "b.txt").unlink()
    commit(template)

    result, output_file, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    outputs = parse_outputs(output_file)
    assert outputs["has_deletions"] == "true"
    assert "config/b.txt" in outputs["deleted_files"]


def test_excluded_paths_are_not_synced(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"

    write(template / "config" / "a.txt", "from template\n")
    write(template / "other" / "b.txt", "also from template\n")
    commit(template)

    result, output_file, _ = run_sync(
        child, template, sync_paths="config other", exclude_paths="other"
    )
    assert result.returncode == 0, result.stderr

    assert (child / "config" / "a.txt").exists()
    assert not (child / "other" / "b.txt").exists()


def test_writes_template_version(workdir: Path) -> None:
    child = workdir / "child"
    template = workdir / "template"
    write(template / "config" / "a.txt", "x\n")
    sha = commit(template)

    result, _, _ = run_sync(child, template, sync_paths="config")
    assert result.returncode == 0, result.stderr

    assert (child / ".template-version").read_text().strip() == sha
