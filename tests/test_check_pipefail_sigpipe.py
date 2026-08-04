"""Tests for .github/scripts/check-pipefail-sigpipe.py (the SIGPIPE lint)."""

import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml

from tests._helpers import REPO_ROOT

LINT = REPO_ROOT / ".github" / "scripts" / "check-pipefail-sigpipe.py"
PRECOMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
HOOK_ID = "check-pipefail-sigpipe"

PIPEFAIL = "set -euo pipefail\n"


def run_lint(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    target = tmp_path / "sample.sh"
    target.write_text(source)
    return subprocess.run(
        [sys.executable, str(LINT), str(target)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "pipeline",
    [
        "producer | head -5",
        "producer | head",
        "producer | head -n1",
        "producer | head -c 2000",
        "producer | head --lines=20",
        "producer | grep -q pattern",
        "producer | grep -im3 pattern",
        "producer | grep -m 3 pattern",
        "producer | grep --max-count=2 pattern",
        "producer | grep -l pattern",
        "producer | grep --quiet pattern",
        "producer | sed -n '5q'",
        "producer | sed '10q'",
        "producer | sed -e '1d' -e '3q'",
        "producer | sed -n '/marker/{p;q}'",
        # The killable writer need not be the pipeline's first stage.
        "producer | tr -d x | head -3",
        # Decorations that do not change what actually runs.
        "producer | LC_ALL=C grep -q pattern",
        "producer | command head -2",
        "producer |& head -1",
        "producer \\\n  | head -1",
    ],
    ids=lambda p: p.replace(" ", "_")[:44],
)
def test_flags_early_exiting_consumers(tmp_path: Path, pipeline: str) -> None:
    result = run_lint(tmp_path, PIPEFAIL + pipeline + "\n")
    assert result.returncode == 1, result.stdout
    assert "SIGPIPE under `set -o pipefail`" in result.stderr


@pytest.mark.parametrize(
    "pipeline",
    [
        # Consumers that read to EOF: no early close, no SIGPIPE.
        "producer | grep pattern",
        "producer | grep -v pattern",
        "producer | grep -c pattern",
        "producer | awk 'NR <= 20'",
        "producer | sed -n '1p'",
        "producer | sed '$d'",
        # A `q` in an s/// replacement, a regex address, or a y/// map is not
        # the quit command — only a real parse can tell those apart.
        "producer | sed 's/^q//'",
        "producer | sed '/quit/d'",
        "producer | sed 'y/abc/qrs/'",
        "producer | sed 's|a|b|;s|q|x|'",
        # GNU `-N` prints all but the last N, so it must read to EOF.
        "producer | head -n -5",
        "producer | head --lines=-5",
        # Reads a file, never the pipe.
        "producer | head -5 <other",
        "producer | grep -q pattern somefile",
        "producer | sed -n '5q' somefile",
        # First stage: nothing upstream for it to kill.
        "head -5 somefile | producer",
        # A pattern that only looks like one: `-q` here is the search string.
        "producer | grep -e -q somefile",
        # Text that is not code.
        "echo 'producer | head -5'",
        "# producer | head -5",
    ],
    ids=lambda p: p.replace(" ", "_")[:44],
)
def test_allows_consumers_that_read_to_eof(tmp_path: Path, pipeline: str) -> None:
    result = run_lint(tmp_path, PIPEFAIL + pipeline + "\n")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "prologue",
    ["#!/bin/bash\n", "set -eu\n", "set +o pipefail\n"],
    ids=["no-set-options", "no-pipefail", "pipefail-only-disabled"],
)
def test_silent_without_pipefail(tmp_path: Path, prologue: str) -> None:
    """Without pipefail a SIGPIPEd producer cannot fail the pipeline, so the
    pattern is not a defect and the lint must stay quiet."""
    result = run_lint(tmp_path, prologue + "producer | head -5\n")
    assert result.returncode == 0, result.stderr


def test_a_script_that_ever_enables_pipefail_is_checked_throughout(
    tmp_path: Path,
) -> None:
    """Pins the deliberate conservatism: enabling pipefail makes the whole file
    checked, because knowing the option's value at one pipeline needs real
    dataflow. A pipeline genuinely run with it off takes the opt-out."""
    source = PIPEFAIL + "set +o pipefail\nproducer | head -5\n"
    assert run_lint(tmp_path, source).returncode == 1
    exempted = (
        PIPEFAIL
        + "set +o pipefail\n# sigpipe-ok: pipefail is off here\nproducer | head -5\n"
    )
    assert run_lint(tmp_path, exempted).returncode == 0


@pytest.mark.parametrize(
    "source",
    [
        PIPEFAIL + "# sigpipe-ok: producer emits one line\nproducer | head -5\n",
        PIPEFAIL + "producer | head -5 # sigpipe-ok: producer emits one line\n",
        PIPEFAIL + "# sigpipe-ok: bounded\nproducer \\\n  | head -5\n",
    ],
    ids=["comment-above", "trailing-comment", "continuation"],
)
def test_opt_out_comment_suppresses(tmp_path: Path, source: str) -> None:
    result = run_lint(tmp_path, source)
    assert result.returncode == 0, result.stderr


# The hook's `files:` pattern must match the extensionless git hooks, so it also
# matches anything else sitting beside them. Parsing a non-shell neighbour as
# bash turns a `| head` inside a prose code span into a reported pipeline.
def test_ignores_a_non_shell_file(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("# Hooks\n\nRun `set -o pipefail`, then `producer | head -5`.\n")
    result = subprocess.run(
        [sys.executable, str(LINT), str(doc)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "shebang",
    ["#!/bin/bash\n", "#!/bin/sh\n", "#!/usr/bin/env bash\n"],
    ids=["bash", "sh", "env-bash"],
)
def test_checks_an_extensionless_shell_script(tmp_path: Path, shebang: str) -> None:
    """The skip above must not swallow the git hooks it exists alongside: they
    carry no extension, so the shebang is the only thing marking them shell."""
    hook = tmp_path / "pre-commit"
    hook.write_text(shebang + PIPEFAIL + "producer | head -5\n")
    result = subprocess.run(
        [sys.executable, str(LINT), str(hook)], capture_output=True, text=True
    )
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source",
    [
        # The reason is mandatory, so the exemption stays review-visible.
        PIPEFAIL + "producer | head -5 # sigpipe-ok:\n",
        PIPEFAIL + "producer | head -5 # sigpipe-ok\n",
        # A marker two lines up is too far to be read as attached.
        PIPEFAIL + "# sigpipe-ok: bounded\necho hi\nproducer | head -5\n",
        # In a string it is not a comment at all.
        PIPEFAIL + "echo '# sigpipe-ok: bounded'\nproducer | head -5\n",
    ],
    ids=["no-reason", "no-colon", "too-far", "inside-string"],
)
def test_opt_out_requires_an_adjacent_reasoned_comment(
    tmp_path: Path, source: str
) -> None:
    result = run_lint(tmp_path, source)
    assert result.returncode == 1, result.stdout


def test_reports_file_and_line(tmp_path: Path) -> None:
    source = PIPEFAIL + "\necho hi\nproducer | head -5\n"
    result = run_lint(tmp_path, source)
    assert result.returncode == 1
    assert "sample.sh:4:" in result.stderr


def test_reports_every_offender(tmp_path: Path) -> None:
    source = PIPEFAIL + "a | head -2\nb | grep pattern\nc | sed '1q'\n"
    result = run_lint(tmp_path, source)
    assert result.returncode == 1
    assert [line.split(":")[1] for line in result.stderr.splitlines()] == ["2", "4"]


def shell_files() -> list[str]:
    """Every tracked file the pre-commit hook's own `files:` pattern selects.

    Reading the pattern from the config (rather than restating a glob here)
    keeps the dogfood set and the enforced set the same one.
    """
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    hooks = [h for repo in config["repos"] for h in repo["hooks"] if h["id"] == HOOK_ID]
    assert len(hooks) == 1, f"{HOOK_ID} must be configured exactly once"
    pattern = re.compile(hooks[0]["files"])
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [p for p in tracked if pattern.search(p)]


def test_tests_run_the_parser_version_the_hook_pins() -> None:
    """Every grammar these tests exercise must be the one the hook installs, or
    they certify behaviour CI never runs. Checking the *installed* version
    (rather than pyproject's declaration) tests the fact that matters, and
    iterating the hook's own pin list covers a future addition without naming
    it."""
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    hook = next(
        h for repo in config["repos"] for h in repo["hooks"] if h["id"] == HOOK_ID
    )
    pins = hook["additional_dependencies"]
    assert pins, "the hook must pin its parser explicitly"
    for pin in pins:
        name, _, pinned = pin.partition("==")
        assert pinned, f"{name} must be pinned to an exact version"
        assert version(name) == pinned


def test_hook_pattern_covers_the_shell_surface() -> None:
    """Non-vacuity for the dogfood below: the pattern really selects the shell
    tree, including the extensionless git hooks it would be easiest to miss."""
    selected = set(shell_files())
    assert {
        "setup.sh",
        ".hooks/pre-commit",
        ".hooks/commit-msg",
        ".hooks/pre-push",
        ".github/scripts/version-bump.sh",
        ".github/scripts/lib/retry.bash",
        ".claude/hooks/session-setup.sh",
    } <= selected
    assert len(selected) > 50


def test_repo_shell_tree_is_clean() -> None:
    """Dogfood: the lint passes over every shell file in this repo."""
    result = subprocess.run(
        [sys.executable, str(LINT), *shell_files()],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
