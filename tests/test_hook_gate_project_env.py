"""Behavior tests for _hook_gate.hooks_needing_the_project_env — the list of
pre-commit hook ids the resolve job must SKIP.

That job holds a write token and deliberately runs no `uv sync`, so a hook whose
entry runs `uv run` would let the pull request choose what the job installs and
then executes. The list is derived from the checked-out `.pre-commit-config.yaml`
rather than written beside it in the workflow, and these drive that derivation
over real config files.

# covers: .github/resolver/auto-resolve/_hook_gate.py
"""

import subprocess
import sys
import textwrap
from pathlib import Path

from tests._helpers import REPO_ROOT

MODULE_DIR = REPO_ROOT / ".github" / "resolver" / "auto-resolve"


def hooks_to_skip(
    tmp_path: Path, config: str | None, pythonpath: str = ""
) -> list[str]:
    """Run the real function in a fresh interpreter whose cwd is `tmp_path`.

    A subprocess, not an import: the module resolves its config relative to the
    process's working directory, and `pythonpath` is how a caller drives it
    under an interpreter that is missing a module.
    """
    if config is not None:
        (tmp_path / ".pre-commit-config.yaml").write_text(config, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(MODULE_DIR)!r});"
            " import _hook_gate;"
            " print('\\n'.join(_hook_gate.hooks_needing_the_project_env()))",
        ],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": pythonpath},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.split()


CONFIG = textwrap.dedent("""\
    repos:
      - repo: local
        hooks:
          - id: ruff-check
            entry: uv run ruff check
          - id: shfmt
            entry: shfmt -d
      - repo: local
        hooks:
          - id: pytest-fast
            entry: uv run pytest -q
""")


def test_every_uv_run_hook_is_named_and_no_other_is(tmp_path: Path) -> None:
    """Both repo blocks are read, and a hook that does not run `uv run` stays
    runnable — refusing every hook would leave the resolution unlinted."""
    assert hooks_to_skip(tmp_path, CONFIG) == ["pytest-fast", "ruff-check"]


def test_a_repository_with_no_precommit_config_refuses_nothing(tmp_path: Path) -> None:
    """`pre-commit run` finds no hook either, so this is an empty set rather than
    a bypassed one."""
    assert hooks_to_skip(tmp_path, None) == []


def test_a_config_free_repository_needs_no_yaml_parser(tmp_path: Path) -> None:
    """PyYAML is installed by the resolve job's install-hook-tools.sh and by
    nothing else, so a calling repository that ships no pre-commit config must
    reach the empty answer without one. A module-scope `import yaml` breaks this
    and takes every `bundle.py` run in that repository with it."""
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(
        textwrap.dedent("""\
            import sys


            class _BlockYaml:
                def find_spec(self, name, path=None, target=None):
                    if name == "yaml" or name.startswith("yaml."):
                        raise ModuleNotFoundError("No module named 'yaml'")
                    return None


            sys.meta_path.insert(0, _BlockYaml())
        """),
        encoding="utf-8",
    )
    # The blocker bites, so a pass below is a run without PyYAML rather than one
    # around this fixture.
    probe = subprocess.run(
        [sys.executable, "-c", "import yaml"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(blocker)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode != 0

    assert hooks_to_skip(tmp_path, None, pythonpath=str(blocker)) == []
