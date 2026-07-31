""".github/actions/claude-run — the execution-log gate as a choke point.

A green claude-code-action step is not proof Claude ran (auth failure, crash
before the result event, corrupt log all exit 0), so every invocation must be
gated. The gate therefore lives INSIDE the shared claude-run action rather than
being re-typed at each call site, where it was previously missed by 5 of 7
callers. These tests enumerate every call site so a future one that opts out
(or a refactor that drops the gate step) reds without being named here.
"""

import yaml

from tests._helpers import REPO_ROOT

ACTION_DIR = REPO_ROOT / ".github" / "actions" / "claude-run"
ACTION = ACTION_DIR / "action.yaml"
GATE_SCRIPT_REL = "../../scripts/check-claude-execution.sh"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _gate_step():
    steps = _load(ACTION)["runs"]["steps"]
    gates = [s for s in steps if GATE_SCRIPT_REL in str(s.get("run", ""))]
    assert len(gates) == 1, f"expected exactly one gate step, found {len(gates)}"
    return gates[0]


def _all_steps():
    """Every step in every workflow and composite action, as (label, step)."""
    roots = [
        (REPO_ROOT / ".github" / "workflows", lambda d: (d.get("jobs") or {}).values()),
        (REPO_ROOT / ".github" / "actions", lambda d: [d.get("runs") or {}]),
    ]
    for directory, containers in roots:
        for path in sorted(directory.rglob("*.y*ml")):
            doc = _load(path)
            if not isinstance(doc, dict):
                continue
            for container in containers(doc):
                if not isinstance(container, dict):
                    continue
                for step in container.get("steps") or []:
                    if isinstance(step, dict):
                        yield f"{path.relative_to(REPO_ROOT)}:{step.get('name')}", step


def _claude_run_call_sites():
    return [
        (label, step)
        for label, step in _all_steps()
        if step.get("uses") == "./.github/actions/claude-run"
    ]


def test_gate_script_path_resolves_from_the_action_directory() -> None:
    """The gate's ${GITHUB_ACTION_PATH}-relative path must land on the real
    script. RED if the script moves or the ../../ depth is wrong — a broken path
    would fail every Claude run, or (worse) be 'fixed' by deleting the gate."""
    assert (ACTION_DIR / GATE_SCRIPT_REL).resolve().is_file()


def test_gate_is_on_by_default() -> None:
    """Fail closed: a caller that says nothing still gets gated."""
    assert _load(ACTION)["inputs"]["gate_execution"]["default"] == "true"


def test_gate_step_is_guarded_only_by_the_opt_out() -> None:
    """The gate must run on every invocation except an explicit opt-out — not be
    narrowed by some other condition that could silently disable it."""
    assert _gate_step()["if"] == "inputs.gate_execution == 'true'"


def test_every_claude_run_call_site_inherits_the_gate() -> None:
    """The choke-point property, asserted member by member: no call site opts
    out. A new caller is gated by construction; one that sets
    gate_execution: false must justify itself here."""
    call_sites = _claude_run_call_sites()
    assert len(call_sites) >= 6, f"expected the known callers, found {len(call_sites)}"
    opted_out = [
        label
        for label, step in call_sites
        if str((step.get("with") or {}).get("gate_execution", "true")).lower()
        == "false"
    ]
    assert opted_out == [], f"ungated claude-run call sites: {opted_out}"


# The one caller that invokes the gate SCRIPT directly, and why it cannot go
# through the claude-run composite: its workspace is the untrusted PR head left
# mid-merge, and the runner reads a local action's manifest out of the workspace
# at step time — so a PR whose conflict lands in an action.yaml would hand the
# runner a manifest full of conflict markers and kill the resolver before it
# starts. It runs the same one script from the base-ref staging dir; the gate is
# still not re-typed, only reached by a different path.
GATE_DIRECT_CALLERS = {
    ".github/workflows/auto-resolve-conflicts.yaml:"
    "Fail loud if the Claude resolution errored",
}


def test_no_call_site_rehand_rolls_the_gate() -> None:
    """The obligation lives in one place. A workflow re-typing the gate means the
    choke point leaked back out into per-caller boilerplate."""
    rehandrolled = [
        label
        for label, step in _all_steps()
        if "check-claude-execution.sh" in str(step.get("run", ""))
        and GATE_SCRIPT_REL not in str(step.get("run", ""))
        and label not in GATE_DIRECT_CALLERS
    ]
    assert rehandrolled == [], f"gate re-implemented at: {rehandrolled}"


def test_every_direct_gate_caller_is_still_a_real_step() -> None:
    """An exemption that outlives its step is an exemption nobody notices has
    stopped meaning anything — and the next caller to re-type the gate would
    inherit it silently."""
    labels = {label for label, _ in _all_steps()}
    assert GATE_DIRECT_CALLERS <= labels, (
        f"stale gate exemption(s): {GATE_DIRECT_CALLERS - labels}"
    )


def test_execution_log_path_has_a_single_source() -> None:
    """The rung-coalesce is computed once and read by both the execution_file
    output and the gate — not copied per consumer, where the copies could drift
    and silently gate a different log than the caller reports on."""
    doc = _load(ACTION)
    assert doc["outputs"]["execution_file"]["value"] == (
        "${{ steps.resolve_log.outputs.execution_file }}"
    )
    assert _gate_step()["env"]["EXECUTION_FILE"] == (
        "${{ steps.resolve_log.outputs.execution_file }}"
    )
