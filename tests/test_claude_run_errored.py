"""Behavior tests for .github/scripts/claude-run-errored.sh — the retry-decision
helper that decides whether a claude-code-action attempt failed hard enough to
retry on the next credential rung. It reads the execution log (the only signal,
since the action exits 0 even on is_error) and writes errored=true/false plus
zero_cost=true/false to GITHUB_OUTPUT; it never fails the step.

Drives the real script as a subprocess and asserts the GITHUB_OUTPUT values.

# covers: .github/scripts/claude-run-errored.sh
"""

import json
import os
import subprocess
from pathlib import Path

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "claude-run-errored.sh"


def run_decider(tmp_path: Path, **env_overrides: str) -> dict[str, str]:
    """Run the real script with a fresh GITHUB_OUTPUT and EXECUTION_FILE scrubbed
    from the inherited env; return the recorded outputs (errored, zero_cost)."""
    gh_out = tmp_path / "github_output"
    env = {k: v for k, v in os.environ.items() if k != "EXECUTION_FILE"}
    env["GITHUB_OUTPUT"] = str(gh_out)
    env.update(env_overrides)
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    lines = gh_out.read_text(encoding="utf-8").strip().splitlines()
    return dict(line.split("=", 1) for line in lines if line)


def write_log(tmp_path: Path, payload: object) -> Path:
    log = tmp_path / "execution.json"
    log.write_text(json.dumps(payload), encoding="utf-8")
    return log


def test_unset_execution_file_is_errored(tmp_path: Path) -> None:
    # No log ⇒ a crash that never reached the model: errored AND provably free.
    out = run_decider(tmp_path)
    assert out["errored"] == "true"
    assert out["zero_cost"] == "true"


def test_missing_file_is_errored(tmp_path: Path) -> None:
    out = run_decider(tmp_path, EXECUTION_FILE=str(tmp_path / "absent.json"))
    assert out["errored"] == "true"
    assert out["zero_cost"] == "true"


def test_empty_file_is_errored(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    out = run_decider(tmp_path, EXECUTION_FILE=str(empty))
    assert out["errored"] == "true"
    assert out["zero_cost"] == "true"


def test_is_error_true_zero_cost_is_errored_and_free(tmp_path: Path) -> None:
    # is_error with no billed inference: retryable, and free to retry (same token).
    log = write_log(tmp_path, {"type": "result", "is_error": True, "total_cost_usd": 0})
    out = run_decider(tmp_path, EXECUTION_FILE=str(log))
    assert out["errored"] == "true"
    assert out["zero_cost"] == "true"


def test_is_error_true_with_cost_is_errored_not_free(tmp_path: Path) -> None:
    # A run that spent real money before erroring is retryable only on a DISTINCT
    # credential — zero_cost=false so the same-token free retry does not fire.
    log = write_log(
        tmp_path, {"type": "result", "is_error": True, "total_cost_usd": 0.2}
    )
    out = run_decider(tmp_path, EXECUTION_FILE=str(log))
    assert out["errored"] == "true"
    assert out["zero_cost"] == "false"


def test_is_error_false_is_not_errored(tmp_path: Path) -> None:
    log = write_log(
        tmp_path, {"type": "result", "is_error": False, "total_cost_usd": 0.1}
    )
    out = run_decider(tmp_path, EXECUTION_FILE=str(log))
    assert out["errored"] == "false"
    assert out["zero_cost"] == "false"


def test_array_form_last_result_wins(tmp_path: Path) -> None:
    log = write_log(
        tmp_path,
        [
            {"type": "system", "subtype": "init"},
            {"type": "result", "is_error": False, "total_cost_usd": 0.1},
        ],
    )
    out = run_decider(tmp_path, EXECUTION_FILE=str(log))
    assert out["errored"] == "false"
    assert out["zero_cost"] == "false"


def test_is_error_absent_is_not_errored(tmp_path: Path) -> None:
    # A result with no is_error field is not an error run, so no retry.
    log = write_log(tmp_path, {"type": "result", "total_cost_usd": 0.5})
    out = run_decider(tmp_path, EXECUTION_FILE=str(log))
    assert out["errored"] == "false"
    assert out["zero_cost"] == "false"
