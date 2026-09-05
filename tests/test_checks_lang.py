"""Tests for the language lints ported into `.github/scripts/checks/`: at
least one flagged input and one passing input per check, run against
synthetic tmp_path content."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests._helpers import REPO_ROOT, commit_files, init_test_repo

CHECKS_DIR = REPO_ROOT / ".github" / "scripts" / "checks"


def _load(name: str):
    src = CHECKS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKS_DIR / f"{name}.py"), *args],
        capture_output=True,
        text=True,
    )


# ── comment-block-length ─────────────────────────────────────────────────


def test_comment_block_length_flags_an_over_cap_note(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        '"""doc."""\n\ndef foo():\n'
        + "".join(f"    # line {n} of prose that is not a list\n" for n in range(1, 7))
        + "    x = 1\n    return x\n",
        encoding="utf-8",
    )
    result = _run("comment-block-length", str(f))
    assert result.returncode == 1
    assert f"{f}: 1 violations exceeds the 0 cap (new)." in result.stderr


def test_comment_block_length_allows_a_short_note(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        '"""doc."""\n\ndef foo():\n    # one short note\n    return 1\n',
        encoding="utf-8",
    )
    result = _run("comment-block-length", str(f))
    assert result.returncode == 0


def test_comment_block_length_header_cap_is_wider_than_note_cap(tmp_path: Path) -> None:
    # A block directly above an exported (non-underscore) def gets the 20-line
    # header cap, so 6 lines there must pass even though the same 6 lines mid-
    # body (asserted above) fail at the 5-line note cap.
    f = tmp_path / "m.py"
    f.write_text(
        '"""doc."""\n\n'
        + "".join(f"# line {n} of header prose\n" for n in range(1, 7))
        + "def foo():\n    return 1\n",
        encoding="utf-8",
    )
    result = _run("comment-block-length", str(f))
    assert result.returncode == 0


# ── _ratchet (shared grandfathered-baseline logic: file-size,
# comment-block-length, unspecified-encoding all import this) ──────────────


def test_ratchet_allows_a_file_at_its_baselined_count() -> None:
    ratchet = _load("_ratchet")
    policy = {"cap": 0, "baseline": {"a.py": 3}}
    assert ratchet.findings({"a.py": 3}, policy, "violations") == []


def test_ratchet_flags_a_file_one_over_its_baselined_count() -> None:
    ratchet = _load("_ratchet")
    policy = {"cap": 0, "baseline": {"a.py": 3}}
    findings = ratchet.findings({"a.py": 4}, policy, "violations")
    assert len(findings) == 1 and "grew past its baseline of 3" in findings[0]


def test_ratchet_flags_an_unbaselined_file_with_a_violation() -> None:
    ratchet = _load("_ratchet")
    policy = {"cap": 0, "baseline": {}}
    findings = ratchet.findings({"a.py": 1}, policy, "violations")
    assert findings == ["a.py: 1 violations exceeds the 0 cap (new)."]


def test_ratchet_flags_a_stale_improved_baseline_entry() -> None:
    ratchet = _load("_ratchet")
    policy = {"cap": 0, "baseline": {"a.py": 3}}
    findings = ratchet.findings({"a.py": 0}, policy, "violations")
    assert len(findings) == 1 and "entry is stale" in findings[0]


def test_ratchet_flags_a_baseline_entry_for_a_deleted_file_in_a_complete_scan() -> None:
    ratchet = _load("_ratchet")
    policy = {"cap": 0, "baseline": {"gone.py": 2}}
    findings = ratchet.findings({}, policy, "violations", complete=True)
    assert len(findings) == 1 and "no matching file" in findings[0]


def test_ratchet_a_partial_scan_ignores_an_untouched_baseline_entry() -> None:
    """`complete=False` (a partial/argv-scoped scan) must not treat a
    baselined file simply absent from THIS run's file list as deleted."""
    ratchet = _load("_ratchet")
    policy = {"cap": 0, "baseline": {"untouched.py": 2}}
    findings = ratchet.findings({}, policy, "violations", complete=False)
    assert findings == []


def test_ratchet_load_policy_fails_loudly_on_a_missing_file(tmp_path: Path) -> None:
    ratchet = _load("_ratchet")
    with pytest.raises(ratchet.BaselineError):
        ratchet.load_policy(tmp_path / "missing.json")


def test_ratchet_load_policy_fails_loudly_on_unparseable_json(tmp_path: Path) -> None:
    ratchet = _load("_ratchet")
    bad = tmp_path / "baseline.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ratchet.BaselineError):
        ratchet.load_policy(bad)


# ── comment-block-length's own ratchet plumbing (scan_counts + findings) ──


def test_comment_block_length_ratchet_passes_a_file_at_its_baseline(
    tmp_path: Path,
) -> None:
    mod = _load("comment-block-length")
    f = tmp_path / "m.py"
    f.write_text(
        '"""doc."""\n\ndef foo():\n'
        + "".join(f"    # line {n} of prose that is not a list\n" for n in range(1, 7))
        + "    x = 1\n    return x\n",
        encoding="utf-8",
    )
    rel = str(f)
    policy = {"cap": 0, "baseline": {rel: 1}}
    counts = mod.scan_counts([rel])
    assert counts == {rel: 1}
    assert mod.findings(counts, policy, complete=False) == []


def test_comment_block_length_ratchet_flags_growth_past_baseline(
    tmp_path: Path,
) -> None:
    mod = _load("comment-block-length")
    f = tmp_path / "m.py"
    f.write_text(
        '"""doc."""\n\ndef foo():\n'
        + "".join(f"    # line {n} of prose that is not a list\n" for n in range(1, 7))
        + "    x = 1\n    return x\n",
        encoding="utf-8",
    )
    rel = str(f)
    policy = {"cap": 0, "baseline": {rel: 0}}
    counts = mod.scan_counts([rel])
    findings = mod.findings(counts, policy, complete=False)
    assert len(findings) == 1 and "grew past its baseline" in findings[0]


# ── big-tuple-annotations ────────────────────────────────────────────────


def test_big_tuple_flags_three_element_tuple(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        "def foo(x: tuple[str, int, bool]) -> None:\n    pass\n", encoding="utf-8"
    )
    result = _run("big-tuple-annotations", str(f))
    assert result.returncode == 1
    assert f"{f}:1:" in result.stderr


def test_big_tuple_allows_variadic_tuple(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("def foo(x: tuple[int, ...]) -> None:\n    pass\n", encoding="utf-8")
    result = _run("big-tuple-annotations", str(f))
    assert result.returncode == 0


def test_big_tuple_respects_suppression_comment(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        "def foo(x: tuple[str, int, bool]) -> None:  # big-tuple-ok: table row\n    pass\n",
        encoding="utf-8",
    )
    result = _run("big-tuple-annotations", str(f))
    assert result.returncode == 0


# ── unspecified-encoding ─────────────────────────────────────────────────


def test_unspecified_encoding_flags_bare_read_text(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("from pathlib import Path\nPath('x').read_text()\n", encoding="utf-8")
    result = _run("unspecified-encoding", str(f))
    assert result.returncode == 1
    assert f"{f}: 1 violations exceeds the 0 cap (new)." in result.stderr


def test_unspecified_encoding_allows_explicit_utf8(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        "from pathlib import Path\nPath('x').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    result = _run("unspecified-encoding", str(f))
    assert result.returncode == 0


# ── unreset-module-state ─────────────────────────────────────────────────


def test_unreset_module_state_flags_a_write_with_no_reset(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        "_CACHE = {}\n\n\ndef record(k, v):\n    _CACHE[k] = v\n", encoding="utf-8"
    )
    result = _run("unreset-module-state", str(f))
    assert result.returncode == 1
    assert f"{f}:1:" in result.stderr


def test_unreset_module_state_allows_a_module_that_declares_reset(
    tmp_path: Path,
) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        "_CACHE = {}\n\n\ndef record(k, v):\n    _CACHE[k] = v\n\n\n"
        "def _reset_process_state():\n    _CACHE.clear()\n",
        encoding="utf-8",
    )
    result = _run("unreset-module-state", str(f))
    assert result.returncode == 0


# ── duplicate-module-constant ────────────────────────────────────────────


def test_duplicate_module_constant_flags_second_binding(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("TIMEOUT = 5\nTIMEOUT = 10\n", encoding="utf-8")
    result = _run("duplicate-module-constant", str(f))
    assert result.returncode == 1
    assert f"{f}:2:" in result.stderr


def test_duplicate_module_constant_allows_accumulation(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("__all__ = ['a']\n__all__ = __all__ + ['b']\n", encoding="utf-8")
    result = _run("duplicate-module-constant", str(f))
    assert result.returncode == 0


# ── duplicate-class-names (whole-tree, needs a git repo for `git ls-files`) ──


def test_duplicate_class_names_flags_a_name_defined_twice(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    init_test_repo(repo)
    commit_files(
        repo,
        {
            ".github/scripts/a.py": "class Widget:\n    pass\n",
            ".github/scripts/b.py": "class Widget:\n    pass\n",
        },
        "add collision",
    )
    mod = _load("duplicate-class-names")
    monkeypatch.chdir(repo)
    collisions = {rel: names for rel, names in mod.scan_tree().items() if names}
    assert collisions == {
        ".github/scripts/a.py": ["Widget"],
        ".github/scripts/b.py": ["Widget"],
    }


def test_duplicate_class_names_allows_a_unique_name(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    init_test_repo(repo)
    commit_files(
        repo,
        {
            ".github/scripts/a.py": "class Widget:\n    pass\n",
            ".github/scripts/b.py": "class Gadget:\n    pass\n",
        },
        "add distinct classes",
    )
    mod = _load("duplicate-class-names")
    monkeypatch.chdir(repo)
    collisions = {rel: names for rel, names in mod.scan_tree().items() if names}
    assert collisions == {}


# ── test-helper-kwargs (whole-tree over a synthetic tests/ dir) ─────────


def test_test_helper_kwargs_allows_matching_call(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "helpers.py").write_text(
        "def helper(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_x.py").write_text(
        "from tests.helpers import helper\n\n\ndef test_it():\n    helper(a=1, b=2)\n",
        encoding="utf-8",
    )
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
    mod = _load("test-helper-kwargs")
    hits = mod.findings(repo / "tests")
    assert hits == []


def test_test_helper_kwargs_flags_mismatched_call_over_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "helpers.py").write_text(
        "def helper(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests" / "test_x.py").write_text(
        "from tests.helpers import helper\n\n\ndef test_it():\n    helper(a=1, c=2)\n",
        encoding="utf-8",
    )
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
    mod = _load("test-helper-kwargs")
    hits = mod.findings(repo / "tests")
    # helper(a, b) called as helper(a=1, c=2): `c` is unknown AND `b` is
    # missing — both are distinct TypeError-causing mismatches on one call.
    problems = {hit.problem for hit in hits}
    assert {hit.callee for hit in hits} == {"helper"}
    assert problems == {"has no parameter `c`", "needs `b`"}


# ── wall-clock-assertions ────────────────────────────────────────────────


def test_wall_clock_flags_elapsed_less_than_literal(tmp_path: Path) -> None:
    f = tmp_path / "test_x.py"
    f.write_text(
        "import time\n\n\ndef test_it():\n    start = time.monotonic()\n"
        "    do_work()\n    elapsed = time.monotonic() - start\n    assert elapsed < 2\n",
        encoding="utf-8",
    )
    result = _run("wall-clock-assertions", str(f))
    assert result.returncode == 1
    assert f"{f}:8:" in result.stderr


def test_wall_clock_allows_a_deadline_poll(tmp_path: Path) -> None:
    f = tmp_path / "test_x.py"
    f.write_text(
        "import time\n\n\ndef test_it():\n    deadline = time.monotonic() + 5\n"
        "    while time.monotonic() < deadline:\n        pass\n",
        encoding="utf-8",
    )
    result = _run("wall-clock-assertions", str(f))
    assert result.returncode == 0


def test_wall_clock_js_flags_date_now_delta(tmp_path: Path) -> None:
    f = tmp_path / "x.test.mjs"
    f.write_text(
        "const start = Date.now();\ndoWork();\nassert(Date.now() - start < 2000);\n",
        encoding="utf-8",
    )
    result = _run("wall-clock-assertions", str(f))
    assert result.returncode == 1
    assert f"{f}:3:" in result.stderr


def test_wall_clock_js_allows_plain_assertion(tmp_path: Path) -> None:
    f = tmp_path / "x.test.mjs"
    f.write_text("assert(result.length === 3);\n", encoding="utf-8")
    result = _run("wall-clock-assertions", str(f))
    assert result.returncode == 0


# ── relative-imports (Node) ──────────────────────────────────────────────


def _run_relative_imports(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    target = tmp_path / "sample.mjs"
    target.write_text(source, encoding="utf-8")
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        "import { findProblems } from "
        f"{str(CHECKS_DIR / 'relative-imports.mjs')!r};\n"
        f"const problems = findProblems({source!r}, 'sample.mjs');\n"
        "process.stdout.write(JSON.stringify(problems));\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["node", str(driver)], cwd=tmp_path, capture_output=True, text=True
    )


def test_relative_imports_flags_missing_target(tmp_path: Path) -> None:
    result = _run_relative_imports(tmp_path, "import { x } from './missing.mjs';\n")
    assert result.returncode == 0, result.stderr
    assert "does not exist" in result.stdout


def test_relative_imports_allows_an_existing_target(tmp_path: Path) -> None:
    (tmp_path / "sibling.mjs").write_text("export const x = 1;\n", encoding="utf-8")
    result = _run_relative_imports(tmp_path, "import { x } from './sibling.mjs';\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
