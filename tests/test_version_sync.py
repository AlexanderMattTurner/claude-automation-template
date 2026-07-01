"""Contract test: tool version pins must agree across every file that pins them.

Versioned tools are pinned in more than one place, and a mismatch makes local
hooks behave differently from CI:

  ruff:
    - .claude/hooks/session-setup.sh   (uv_install_if_missing ruff "ruff==X")
    - .pre-commit-config.yaml          (ruff-pre-commit rev: vX)

  zizmor:
    - .claude/hooks/session-setup.sh   (uv_install_if_missing zizmor "zizmor==X")
    - .pre-commit-config.yaml          (additional_dependencies: ["zizmor==X"])
    - .github/workflows/zizmor.yaml    (uvx zizmor==X)

  python (the interpreter pre-commit's Python-based hooks run under):
    - .python-version
    - .pre-commit-config.yaml          (default_language_version: python:)
    - .github/workflows/pre-commit.yaml (actions/setup-python python-version:)

This test is the machine-checkable form of the "keep in sync" comments in
session-setup.sh and .pre-commit-config.yaml. Each case is checked
independently so a failure names the exact file pair that drifted.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_SETUP = REPO_ROOT / ".claude" / "hooks" / "session-setup.sh"
PRE_COMMIT_CFG = REPO_ROOT / ".pre-commit-config.yaml"
ZIZMOR_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "zizmor.yaml"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"
PRE_COMMIT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pre-commit.yaml"


def _search(pattern: str, path: Path, *, flags: int = 0) -> str:
    """Return the first capture group of *pattern* in *path*, or fail loudly.

    Failing when the pattern matches nothing keeps the test from passing
    vacuously if a source file is restructured and a pin moves or disappears.
    """
    m = re.search(pattern, path.read_text(), flags)
    assert m, f"Pattern {pattern!r} matched nothing in {path}"
    return m.group(1)


def _session_setup_pin(tool: str) -> str:
    """Version pinned for *tool* by an uv_install_if_missing call in session-setup.sh."""
    return _search(
        rf'uv_install_if_missing\s+{re.escape(tool)}\s+"[^"]*?==([^"]+)"',
        SESSION_SETUP,
    )


def _ruff_pins() -> dict[str, str]:
    # rev: sits on the line directly under the repo:, so no DOTALL — keeping the
    # match line-local prevents .* from skipping across to another repo's rev:.
    return {
        "session-setup.sh": _session_setup_pin("ruff"),
        ".pre-commit-config.yaml": _search(
            r"astral-sh/ruff-pre-commit\s+rev:\s+v?(\S+)", PRE_COMMIT_CFG
        ),
    }


def _zizmor_pins() -> dict[str, str]:
    return {
        "session-setup.sh": _session_setup_pin("zizmor"),
        ".pre-commit-config.yaml": _search(r'zizmor==([^"\]]+)"', PRE_COMMIT_CFG),
        "zizmor.yaml": _search(r"uvx zizmor==(\S+)", ZIZMOR_WORKFLOW),
    }


def _python_pins() -> dict[str, str]:
    return {
        ".python-version": PYTHON_VERSION_FILE.read_text().strip(),
        ".pre-commit-config.yaml": _search(
            r"default_language_version:\s*\n\s*python:\s*python(\S+)", PRE_COMMIT_CFG
        ),
        "pre-commit.yaml": _search(
            r'python-version:\s*"?(\S+?)"?\s*$', PRE_COMMIT_WORKFLOW, flags=re.MULTILINE
        ),
    }


@pytest.mark.parametrize(
    "pins_fn",
    [_ruff_pins, _zizmor_pins, _python_pins],
    ids=["ruff", "zizmor", "python"],
)
def test_version_pins_agree(pins_fn) -> None:
    pins = pins_fn()
    unique = set(pins.values())
    assert len(unique) == 1, (
        f"Version pins disagree across files: {pins}. Update them together."
    )
