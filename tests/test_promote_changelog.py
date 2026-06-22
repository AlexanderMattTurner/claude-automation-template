"""Tests for .github/scripts/promote-changelog.mjs.

The helper promotes the `## Unreleased` block in CHANGELOG.md to a dated
`## [version]` section after a successful publish (see version-bump.sh). These
drive it the way the release bash script does: CHANGELOG.md in the cwd, release
notes passed through NEW_VERSION / RELEASE_DATE / CHANGELOG_SECTION env vars.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)
SCRIPT = REPO_ROOT / ".github" / "scripts" / "promote-changelog.mjs"

CHANGELOG_HEADER = "# Changelog\n\nIntro prose.\n\n"


def run(
    cwd: Path,
    *,
    version: str = "1.2.3",
    date: str = "2026-06-22",
    section: str = "### Added\n\n- A new flag.",
    env_overrides: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the helper in `cwd` with the given release-note env vars."""
    env = {
        "NEW_VERSION": version,
        "RELEASE_DATE": date,
        "CHANGELOG_SECTION": section,
    }
    if env_overrides is not None:
        env.update(env_overrides)
    # Drop keys whose override value is None so we can test missing vars.
    env = {k: v for k, v in env.items() if v is not None}
    return subprocess.run(
        ["node", str(SCRIPT)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], **env},
    )


def write_changelog(cwd: Path, body: str) -> Path:
    path = cwd / "CHANGELOG.md"
    path.write_text(CHANGELOG_HEADER + body)
    return path


def test_promotes_unreleased_to_dated_section(tmp_path: Path) -> None:
    path = write_changelog(tmp_path, "## Unreleased\n")
    result = run(tmp_path)

    content = path.read_text()
    assert "## Unreleased\n\n## [1.2.3] - 2026-06-22\n" in content
    assert "### Added\n\n- A new flag." in content
    # The empty Unreleased block is preserved at the top for the next cycle.
    assert content.index("## Unreleased") < content.index("## [1.2.3]")
    assert "Promoted Unreleased" in result.stdout


def test_preserves_content_after_unreleased_block(tmp_path: Path) -> None:
    prior = "## [1.0.0] - 2026-01-01\n\n### Added\n\n- Old thing.\n"
    path = write_changelog(tmp_path, f"## Unreleased\n\n{prior}")
    run(tmp_path)

    content = path.read_text()
    # New section lands between Unreleased and the prior release, newest first.
    assert content.index("## [1.2.3]") < content.index("## [1.0.0]")
    assert "- Old thing." in content


def test_unreleased_is_last_heading(tmp_path: Path) -> None:
    # Unreleased runs to EOF (afterBlock is empty); the dated section is still
    # inserted and the empty Unreleased block is preserved above it.
    path = write_changelog(tmp_path, "## Unreleased\n")
    run(tmp_path)

    content = path.read_text()
    assert content.endswith("### Added\n\n- A new flag.\n")
    assert content.index("## Unreleased") < content.index("## [1.2.3]")


def test_strips_model_emitted_version_heading(tmp_path: Path) -> None:
    path = write_changelog(tmp_path, "## Unreleased\n")
    run(tmp_path, section="## [1.2.3] - 2026-06-22\n\n### Fixed\n\n- A bug.")

    content = path.read_text()
    # The body's stray heading is stripped; only the script's heading remains.
    assert content.count("## [1.2.3]") == 1
    assert "### Fixed\n\n- A bug." in content


def test_empty_body_leaves_file_unchanged(tmp_path: Path) -> None:
    path = write_changelog(tmp_path, "## Unreleased\n")
    before = path.read_text()
    result = run(tmp_path, section="   \n  ")

    assert path.read_text() == before
    assert "empty" in result.stderr


def test_missing_unreleased_heading_leaves_file_unchanged(tmp_path: Path) -> None:
    path = write_changelog(tmp_path, "## [1.0.0] - 2026-01-01\n\n- Old.\n")
    before = path.read_text()
    result = run(tmp_path)

    assert path.read_text() == before
    assert 'no "## Unreleased"' in result.stderr


def test_missing_env_var_skips(tmp_path: Path) -> None:
    path = write_changelog(tmp_path, "## Unreleased\n")
    before = path.read_text()
    result = run(tmp_path, env_overrides={"NEW_VERSION": None})

    assert path.read_text() == before
    assert "missing required env var NEW_VERSION" in result.stderr
