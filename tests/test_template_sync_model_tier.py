"""template-sync's model tier needs the `claude` binary, so the job installs it.

template-sync-resolve.sh hands every conflict mergiraf leaves to the resolver's
auto-resolve/fanout.py, which shells out to `claude`. Nothing on the runner
image provides it and no earlier step of the `sync` job installed it, so in
every downstream sync fanout died with "the `claude` CLI is not on PATH", the
model resolved nothing, and the marker gate restored and redded every
non-structural conflict (agent-sanitizer run 39616255001). The pinned installer
already ships to downstream repos via SYNC_PATHS; the job just never ran it.
"""

import yaml

from tests._helpers import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "template-sync.yaml"
INSTALLER = ".github/scripts/install-claude-cli.sh"
RESOLVER = ".github/scripts/template-sync-resolve.sh"


def _sync_steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["sync"]["steps"]


def _runs(step: dict, script: str) -> bool:
    return script in step.get("run", "")


def test_the_claude_cli_is_installed_before_the_model_tier_under_its_gate() -> None:
    steps = _sync_steps()
    resolve_at = [i for i, step in enumerate(steps) if _runs(step, RESOLVER)]
    # Non-vacuity: a renamed resolver script would otherwise leave nothing to
    # order the install against.
    assert len(resolve_at) == 1, f"expected one step running {RESOLVER}"
    installs = [step for step in steps[: resolve_at[0]] if _runs(step, INSTALLER)]
    assert len(installs) == 1, f"no step before the resolve step runs {INSTALLER}"
    # A narrower gate would skip the install on a run where resolve still runs.
    assert installs[0]["if"] == steps[resolve_at[0]]["if"]
