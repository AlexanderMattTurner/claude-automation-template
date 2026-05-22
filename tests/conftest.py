"""Shared fixtures for shell-script tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def init_test_repo(path: Path) -> None:
    """Init a throwaway repo with signing/hooks disabled so fixtures can commit
    in any environment (including CI runners with enforced commit signing)."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    for k, v in [
        ("commit.gpgsign", "false"),
        ("tag.gpgsign", "false"),
        ("user.name", "t"),
        ("user.email", "t@t"),
        ("core.hooksPath", "/dev/null"),
    ]:
        subprocess.run(["git", "config", "--local", k, v], cwd=path, check=True)


def git_env() -> dict:
    return {**os.environ, **GIT_IDENTITY_ENV}


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def empty_git_repo(tmp_path: Path) -> Iterator[Path]:
    """Throwaway git repo with an initial empty commit (so HEAD exists)."""
    init_test_repo(tmp_path)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=tmp_path,
        env=git_env(),
        check=True,
    )
    yield tmp_path


@pytest.fixture
def copy_script() -> Callable[[str, Path], Path]:
    """Return a helper that copies a repo script into a sandbox dir."""
    src_dirs = [
        REPO_ROOT / ".github" / "scripts",
        REPO_ROOT / ".claude" / "hooks",
        REPO_ROOT / ".hooks",
    ]

    def _copy(script_name: str, dest_dir: Path) -> Path:
        for src_dir in src_dirs:
            src = src_dir / script_name
            if src.exists():
                dest = dest_dir / script_name
                shutil.copy2(src, dest)
                dest.chmod(0o755)
                return dest
        raise FileNotFoundError(f"Could not find {script_name} in any known location")

    return _copy
