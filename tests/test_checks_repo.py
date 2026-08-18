"""Tests for the checks ported into .github/scripts/checks/:
file-size, truncating-pr-json, grant-wildcards, sparse-checkout-closure,
gate-hooks-shimmed. Each check gets one input that must be flagged and one
that must pass, driven through the check's own pure functions — mostly
against synthetic content under tmp_path, plus one assertion against this
repo's own `.claude/settings.json` to prove the check accepts real input.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT

_CHECKS = REPO_ROOT / ".github" / "scripts" / "checks"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _CHECKS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


grant_wildcards = _load("grant-wildcards")
truncating_pr_json = _load("truncating-pr-json")
gate_hooks_shimmed = _load("gate-hooks-shimmed")
file_size = _load("file-size")
sparse_checkout_closure = _load("sparse-checkout-closure")


# ── grant-wildcards ──────────────────────────────────────────────────────
def test_grant_wildcards_flags_word_extending_star() -> None:
    text = json.dumps({"permissions": {"allow": ["Bash(git diff*)"]}})
    assert grant_wildcards.violations(text) == [1]


def test_grant_wildcards_accepts_delimiter_star() -> None:
    text = json.dumps({"permissions": {"allow": ["Bash(git diff *)"]}})
    assert grant_wildcards.violations(text) == []


# ── truncating-pr-json ───────────────────────────────────────────────────
def test_truncating_pr_json_flags_files_field() -> None:
    text = "gh pr view 1 --json files\n"
    assert truncating_pr_json.violations(text) == [1]


def test_truncating_pr_json_accepts_bounded_field() -> None:
    text = "gh pr view 1 --json title,state\n"
    assert truncating_pr_json.violations(text) == []


def test_truncating_pr_json_respects_opt_out() -> None:
    text = (
        "# truncating-pr-json-ok: paged separately below\ngh pr view 1 --json files\n"
    )
    assert truncating_pr_json.violations(text) == []


# ── gate-hooks-shimmed ───────────────────────────────────────────────────
def test_gate_hooks_shimmed_flags_raw_gate() -> None:
    settings = {
        "hooks": {
            "PreToolUse": [{"hooks": [{"type": "command", "command": "node gate.mjs"}]}]
        }
    }
    hits = gate_hooks_shimmed.unshimmed_gates(settings)
    assert len(hits) == 1 and "gate.mjs" in hits[0]


def test_gate_hooks_shimmed_accepts_launched_gate() -> None:
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "safe-launch.sh gate.mjs",
                        }
                    ]
                }
            ]
        }
    }
    assert gate_hooks_shimmed.unshimmed_gates(settings) == []


def test_real_settings_are_compliant() -> None:
    assert gate_hooks_shimmed.check_file(REPO_ROOT / ".claude" / "settings.json") == []


# ── file-size ────────────────────────────────────────────────────────────
def test_file_size_flags_new_violator_over_cap() -> None:
    policy = {"cap": 100, "baseline": {}}
    assert file_size.findings({"a.py": 150}, policy) == [
        "a.py: 150 lines exceeds the 100-line cap (new)."
    ]


def test_file_size_accepts_baselined_exact_match() -> None:
    policy = {"cap": 100, "baseline": {"a.py": 150}}
    assert file_size.findings({"a.py": 150}, policy) == []


def test_file_size_flags_grown_baseline_entry() -> None:
    policy = {"cap": 100, "baseline": {"a.py": 150}}
    findings = file_size.findings({"a.py": 200}, policy)
    assert len(findings) == 1 and "grew past its baseline" in findings[0]


def test_file_size_flags_stale_shrunk_baseline_entry() -> None:
    policy = {"cap": 100, "baseline": {"a.py": 150}}
    findings = file_size.findings({"a.py": 120}, policy)
    assert len(findings) == 1 and "stale entry" in findings[0]


def test_file_size_code_line_count_excludes_comments_and_blanks(tmp_path) -> None:
    src = tmp_path / "m.py"
    src.write_text("# a comment\n\nprint(1)\nprint(2)\n", encoding="utf-8")
    assert file_size._code_line_count(src, src.read_text(encoding="utf-8")) == 2


# ── sparse-checkout-closure ──────────────────────────────────────────────
def _write_workflow(tmp_path: Path, sparse: str, run: str) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "w.yaml").write_text(
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - uses: actions/checkout@abc\n"
        "        with:\n"
        f"          sparse-checkout: |\n            {sparse}\n"
        f"      - run: {run}\n",
        encoding="utf-8",
    )


def test_sparse_checkout_closure_flags_uncovered_dependency(tmp_path: Path) -> None:
    _write_workflow(tmp_path, ".github/scripts", "bash .github/actions/x/run.sh")
    (tmp_path / ".github" / "actions" / "x").mkdir(parents=True)
    (tmp_path / ".github" / "actions" / "x" / "run.sh").write_text(
        "echo hi\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        sparse_checkout_closure.main(root=tmp_path)


def test_sparse_checkout_closure_accepts_covered_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_workflow(tmp_path, ".github/scripts", "bash .github/scripts/run.sh")
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    (tmp_path / ".github" / "scripts" / "run.sh").write_text(
        "echo hi\n", encoding="utf-8"
    )
    sparse_checkout_closure.main(root=tmp_path)  # must not raise
    assert capsys.readouterr().out == ""
