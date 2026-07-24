""".github/scripts/check-claude-execution.sh — the claude-code-action execution gate.

Drives the script over synthetic execution logs and asserts the zero-cost
credential-failure distinction: a green claude-code-action step is not proof
Claude ran, so a zero-billed is_error (auth/config), a crash-before-result, or a
corrupt log must red the step — while a non-zero-cost is_error is explicitly a
RUN failure, NOT a credential problem.
"""

import json
import subprocess


from tests._helpers import REPO_ROOT

_SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-claude-execution.sh"


def _run(execution: object | None, tmp_path, extra_env=None):
    """Run the gate over an execution log (None = no file at all). Returns
    (returncode, stderr, github_output_contents)."""
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    out_file = tmp_path / "gh_output"
    out_file.write_text("", encoding="utf-8")
    env["GITHUB_OUTPUT"] = str(out_file)
    if execution is not None:
        exec_file = tmp_path / "execution.json"
        exec_file.write_text(json.dumps(execution), encoding="utf-8")
        env["EXECUTION_FILE"] = str(exec_file)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stderr, out_file.read_text(encoding="utf-8")


def test_zero_cost_is_error_is_a_proven_credential_failure(tmp_path) -> None:
    rc, err, _ = _run({"is_error": True, "total_cost_usd": 0}, tmp_path)
    assert rc == 1
    assert "ZERO billed inference" in err
    assert "credential/config" in err
    assert "sk-ant-oat01-" in err


def test_nonzero_cost_is_error_is_a_run_failure_not_credential(tmp_path) -> None:
    rc, err, _ = _run(
        {"is_error": True, "total_cost_usd": 0.12, "num_turns": 3}, tmp_path
    )
    assert rc == 1
    assert "genuine run failure" in err
    assert "NOT a credential" in err
    # The cost and turn count are surfaced so the reader can see the model ran.
    assert "0.12" in err and "3 turn" in err


def test_missing_cost_field_is_ambiguous(tmp_path) -> None:
    rc, err, _ = _run({"is_error": True}, tmp_path)
    assert rc == 1
    assert "cannot distinguish" in err
    assert "read the execution log" in err


def test_no_execution_file_reds(tmp_path) -> None:
    rc, err, _ = _run(None, tmp_path)
    assert rc == 1
    assert "produced no execution log" in err


def test_corrupt_log_reds(tmp_path) -> None:
    exec_file = tmp_path / "execution.json"
    exec_file.write_text("{not json", encoding="utf-8")
    out_file = tmp_path / "gh_output"
    out_file.write_text("", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "EXECUTION_FILE": str(exec_file),
            "GITHUB_OUTPUT": str(out_file),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "not parseable JSON" in proc.stderr


def test_array_log_uses_last_result_event(tmp_path) -> None:
    # An array of events: the LAST result event is the outcome.
    log = [
        {"type": "assistant"},
        {"type": "result", "is_error": True, "total_cost_usd": 0},
    ]
    rc, err, _ = _run(log, tmp_path)
    assert rc == 1
    assert "ZERO billed inference" in err


def test_array_without_result_event_reds(tmp_path) -> None:
    rc, err, _ = _run([{"type": "assistant"}], tmp_path)
    assert rc == 1
    assert "no result event" in err


def test_success_exports_denials_and_warns(tmp_path) -> None:
    rc, err, gh_out = _run({"is_error": False, "permission_denials_count": 2}, tmp_path)
    assert rc == 0
    assert "permission_denials=2" in gh_out
    assert "2 permission denial" in err


def test_clean_success_exports_zero_and_is_quiet(tmp_path) -> None:
    rc, err, gh_out = _run({"is_error": False}, tmp_path)
    assert rc == 0
    assert "permission_denials=0" in gh_out
    assert err == ""


def test_context_label_is_used(tmp_path) -> None:
    rc, err, _ = _run(
        {"is_error": True, "total_cost_usd": 0},
        tmp_path,
        extra_env={"CONTEXT": "Custom label"},
    )
    assert rc == 1
    assert "Custom label" in err
