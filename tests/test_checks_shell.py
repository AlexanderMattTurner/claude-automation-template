"""Tests for the shell/CI-hygiene lints under .github/scripts/checks/, ported
from agent-glovebox: cwd-scoped-git, dead-shell-functions,
shell-source-declarations, bare-mkdir, env-arith, curl-retry, retry-loop,
unbounded-waits, positional-git-argv, path-shadowed-interpreter, sleep-as-sync.

Each drives the module's own `violations`/`find_dead` entrypoint — the exact
function pre-commit's `main` calls — over synthetic content, asserting one
input flags and one passes.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from tests._helpers import REPO_ROOT

_CHECKS_DIR = REPO_ROOT / ".github" / "scripts" / "checks"


def _load(name: str) -> ModuleType:
    src = _CHECKS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cwd_scoped_git = _load("cwd-scoped-git")
dead_shell_functions = _load("dead-shell-functions")
shell_source_declarations = _load("shell-source-declarations")
bare_mkdir = _load("bare-mkdir")
env_arith = _load("env-arith")
curl_retry = _load("curl-retry")
retry_loop = _load("retry-loop")
unbounded_waits = _load("unbounded-waits")
positional_git_argv = _load("positional-git-argv")
path_shadowed_interpreter = _load("path-shadowed-interpreter")
sleep_as_sync = _load("sleep-as-sync")


# ── cwd-scoped-git ───────────────────────────────────────────────────────
def test_cwd_scoped_git_flags_unscoped_write() -> None:
    text = 'import subprocess\nsubprocess.run(["git", "merge", "--abort"])\n'
    assert cwd_scoped_git.violations(text) == [2]


def test_cwd_scoped_git_accepts_scoped_call() -> None:
    text = (
        'import subprocess\nsubprocess.run(["git", "-C", repo, "merge", "--abort"])\n'
    )
    assert cwd_scoped_git.violations(text) == []


# ── dead-shell-functions ─────────────────────────────────────────────────
def test_dead_shell_functions_flags_uncalled_function(tmp_path: Path) -> None:
    (tmp_path / "lib.sh").write_text("dead_fn() {\n  echo hi\n}\n", encoding="utf-8")
    dead = dead_shell_functions.find_dead(
        dead_shell_functions._load_scan_files(tmp_path)
    )
    assert [d.name for d in dead] == ["dead_fn"]


def test_dead_shell_functions_accepts_called_function(tmp_path: Path) -> None:
    (tmp_path / "lib.sh").write_text("live_fn() {\n  echo hi\n}\n", encoding="utf-8")
    (tmp_path / "run.sh").write_text("live_fn\n", encoding="utf-8")
    dead = dead_shell_functions.find_dead(
        dead_shell_functions._load_scan_files(tmp_path)
    )
    assert dead == []


# ── shell-source-declarations ────────────────────────────────────────────
def test_shell_source_declarations_flags_undeclared_variable_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shell_source_declarations, "REPO_ROOT", tmp_path)
    text = 'LIB="lib.sh"\nsource "$LIB"\n'
    assert shell_source_declarations.violations(text) == [2]


def test_shell_source_declarations_accepts_declared_and_resolving_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shell_source_declarations, "REPO_ROOT", tmp_path)
    (tmp_path / "lib.sh").write_text("# a library\n", encoding="utf-8")
    text = 'LIB="lib.sh"\n# shellcheck source=lib.sh\nsource "$LIB"\n'
    assert shell_source_declarations.violations(text) == []


# ── bare-mkdir ────────────────────────────────────────────────────────────
def test_bare_mkdir_flags_dash_p() -> None:
    assert bare_mkdir.violations('mkdir -p "$dir"\n') == [1]


def test_bare_mkdir_accepts_plain_mkdir() -> None:
    assert bare_mkdir.violations('mkdir "$dir"\n') == []


def test_bare_mkdir_respects_annotation() -> None:
    assert (
        bare_mkdir.violations(
            'mkdir -p "$dir"  # bare-mkdir-ok: cannot source ensure_dir\n'
        )
        == []
    )


# ── env-arith ─────────────────────────────────────────────────────────────
def test_env_arith_flags_all_caps_var_in_arithmetic() -> None:
    assert env_arith.violations("x=$((TIMEOUT_SECONDS + 1))\n") == [1]


def test_env_arith_accepts_local_lowercase_var() -> None:
    assert env_arith.violations("x=$((count + 1))\n") == []


# ── curl-retry ────────────────────────────────────────────────────────────
def test_curl_retry_flags_unretried_download() -> None:
    assert curl_retry.violations("curl -o out.tar https://example.test/x\n") == [1]


def test_curl_retry_accepts_retried_download() -> None:
    assert (
        curl_retry.violations("curl --retry 3 -o out.tar https://example.test/x\n")
        == []
    )


def test_curl_retry_accepts_wrapped_download() -> None:
    assert (
        curl_retry.violations("retry -- curl -o out.tar https://example.test/x\n") == []
    )


# ── retry-loop ────────────────────────────────────────────────────────────
def test_retry_loop_flags_counted_sleep_loop() -> None:
    text = "for i in 1 2 3; do\n  try_thing\n  sleep 2\ndone\n"
    assert retry_loop.violations(text) == [1]


def test_retry_loop_accepts_clock_bound_loop() -> None:
    text = "while (( SECONDS < 30 )); do\n  sleep 1\ndone\n"
    assert retry_loop.violations(text) == []


def test_retry_loop_accepts_unbounded_poll_loop() -> None:
    text = "while true; do\n  check_ready && break\n  sleep 1\ndone\n"
    assert retry_loop.violations(text) == []


# ── unbounded-waits ───────────────────────────────────────────────────────
def test_unbounded_waits_flags_bare_remote_git() -> None:
    assert unbounded_waits.violations("git fetch origin\n") == [1]


def test_unbounded_waits_accepts_timeout_wrapped_git() -> None:
    assert unbounded_waits.violations("timeout 30 git fetch origin\n") == []


def test_unbounded_waits_accepts_local_subcommand() -> None:
    assert unbounded_waits.violations("git status\n") == []


# ── positional-git-argv ──────────────────────────────────────────────────
def test_positional_git_argv_flags_positional_test() -> None:
    # allow-positional-git-argv: fixture text for the check under test, not a real stub
    text = "def test_x():\n    line = '[ \"$1\" = rev-parse ]'\n"
    assert positional_git_argv.violations(text) == [2]


def test_positional_git_argv_accepts_subcommand_search() -> None:
    text = 'def test_x():\n    assert "rev-parse" in argv\n'
    assert positional_git_argv.violations(text) == []


def test_positional_git_argv_ignores_mention_in_comment() -> None:
    # allow-positional-git-argv: fixture text for the check under test, not a real stub
    text = 'def test_x():\n    # e.g. [ "$1" = rev-parse ]\n    pass\n'
    assert positional_git_argv.violations(text) == []


# ── path-shadowed-interpreter ────────────────────────────────────────────
_WORKFLOW_WITH_AGENT_STEP = """
jobs:
  build:
    steps:
      - uses: anthropics/claude-code-action@sha
      - name: run tests
        run: python3 -m pytest
"""

_WORKFLOW_PYTHON_BEFORE_AGENT = """
jobs:
  build:
    steps:
      - name: run tests
        run: python3 -m pytest
      - uses: anthropics/claude-code-action@sha
"""


def test_path_shadowed_interpreter_flags_bare_python_after_agent_step(
    tmp_path: Path,
) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(_WORKFLOW_WITH_AGENT_STEP, encoding="utf-8")
    found = path_shadowed_interpreter.violations(tmp_path)
    # The line is 1-based within the flagged step's own `run:` text, not the
    # whole workflow file — this step's `run:` is a single line.
    assert list(found) == [("ci.yml", 1)]


def test_path_shadowed_interpreter_accepts_python_before_agent_step(
    tmp_path: Path,
) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(_WORKFLOW_PYTHON_BEFORE_AGENT, encoding="utf-8")
    assert path_shadowed_interpreter.violations(tmp_path) == {}


# ── sleep-as-sync ─────────────────────────────────────────────────────────
def test_sleep_as_sync_flags_sleep_before_assert() -> None:
    text = "def test_x():\n    time.sleep(2)\n    assert flag_is_set()\n"
    assert sleep_as_sync.violations(text) == [2]


def test_sleep_as_sync_accepts_sleep_inside_poll_loop() -> None:
    text = (
        "def test_x():\n"
        "    while not flag_is_set():\n"
        "        time.sleep(0.1)\n"
        "    assert flag_is_set()\n"
    )
    assert sleep_as_sync.violations(text) == []


def test_sleep_as_sync_respects_annotation() -> None:
    text = (
        "def test_x():\n"
        "    time.sleep(2)  # allow-sleep: subject IS the timeout\n"
        "    assert flag_is_set()\n"
    )
    assert sleep_as_sync.violations(text) == []
