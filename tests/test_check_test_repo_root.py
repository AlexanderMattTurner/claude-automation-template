"""Tests for .github/scripts/check-test-repo-root.py (the parent-walk lint)."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

LINT = REPO_ROOT / ".github" / "scripts" / "check-test-repo-root.py"


def run_lint(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    target = tmp_path / "test_sample.py"
    target.write_text(source)
    return subprocess.run(
        [sys.executable, str(LINT), str(target)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n",
        "from pathlib import Path\nROOT = Path(__file__).parents[2]\n",
        "from pathlib import Path\nROOT = Path(__file__).resolve().parent.parent\n",
        "from pathlib import Path\nROOT = Path(__file__).parent.parent.parent\n",
        "from pathlib import Path\nROOT = Path(__file__).absolute().parents[0]\n",
    ],
    ids=[
        "resolve-parents",
        "bare-parents",
        "resolve-parent-parent",
        "triple-parent",
        "absolute-parents",
    ],
)
def test_flags_depth_based_walks(tmp_path: Path, source: str) -> None:
    result = run_lint(tmp_path, source)
    assert result.returncode == 1
    assert "depth-based repo-root walk" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        # The sanctioned pattern: one .parent as a cwd anchor for git.
        "from pathlib import Path\nHERE = Path(__file__).parent\n",
        "from pathlib import Path\nHERE = Path(__file__).resolve().parent\n",
        # Walking some other path is not repo-root derivation.
        "from pathlib import Path\np = Path('/x/y')\nq = p.parent.parent\n",
        "from tests._helpers import REPO_ROOT\nX = REPO_ROOT / 'tests'\n",
        # The idiom mentioned in a string/comment must not trip an AST lint.
        's = "Path(__file__).resolve().parents[1]"\n# Path(__file__).parent.parent\n',
    ],
    ids=["single-parent", "resolve-parent", "other-path", "helper", "string-comment"],
)
def test_allows_sanctioned_patterns(tmp_path: Path, source: str) -> None:
    result = run_lint(tmp_path, source)
    assert result.returncode == 0, result.stderr


def test_reports_file_and_line(tmp_path: Path) -> None:
    source = "from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n"
    result = run_lint(tmp_path, source)
    assert result.returncode == 1
    assert "test_sample.py:3:" in result.stderr


def test_repo_test_tree_is_clean() -> None:
    """Dogfood: the lint passes over every test file in this repo (the
    violations it was written against are gone)."""
    test_files = sorted(str(p) for p in (REPO_ROOT / "tests").glob("*.py"))
    assert test_files
    result = subprocess.run(
        [sys.executable, str(LINT), *test_files],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
