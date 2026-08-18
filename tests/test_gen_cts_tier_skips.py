""".github/scripts/gen_cts_tier_skips.py — the tier aggregates' `--skip` lists.

Drives the generator's pure `skips_by_hook` / `render_config` over synthetic
configs and a synthetic tier registry, so the tests do not depend on a
ci-truth-serum clone being present. The real `.pre-commit-config.yaml` is the
contract case: every name an aggregate skips must be a hook the config also
lists on its own, which is the half of the generator's property that is
checkable without the tier registry.
"""

import importlib.util
import textwrap

import pytest
import yaml

from tests._helpers import REPO_ROOT

_SRC = REPO_ROOT / ".github" / "scripts" / "gen_cts_tier_skips.py"
_spec = importlib.util.spec_from_file_location("gen_cts_tier_skips", _SRC)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

TIERS = {
    "1": ["check_workflow_pipefail", "check_pr_paths"],
    "2": ["check_path_gate_deps", "check_failure_notifier_coverage", "check_echo"],
    "extras": ["check_claude_model"],
}


def config_text(*hook_ids: str, marked: tuple[str, ...] = ()) -> str:
    """A config listing HOOK_IDS, with a generated region under each of MARKED."""
    lines = ["repos:", "  - repo: https://github.com/x/ci-truth-serum", "    rev: abc"]
    lines.append("    hooks:")
    for hook_id in hook_ids:
        lines.append(f"      - id: {hook_id}")
        if hook_id in marked:
            lines.append("        args:")
            lines.append(f"          {mod.begin_marker(hook_id)}")
            lines.append("          - --skip")
            lines.append("          - check_stale")
            lines.append(f"          {mod.end_marker(hook_id)}")
    return "\n".join(lines) + "\n"


def skips(*hook_ids: str) -> dict:
    return mod.skips_by_hook(yaml.safe_load(config_text(*hook_ids)), TIERS)


def test_only_separately_listed_members_are_skipped() -> None:
    assert skips("check-tier2", "check-path-gate-deps") == {
        "check-tier2": ["check_path_gate_deps"]
    }


def test_an_aggregate_alone_skips_nothing() -> None:
    assert skips("check-tier1", "check-tier2", "check-extras") == {
        "check-tier1": [],
        "check-tier2": [],
        "check-extras": [],
    }


def test_a_member_is_skipped_only_by_its_own_tier() -> None:
    """`run_tier` exits 2 on a skip name outside the tier it is given."""
    assert skips("check-tier1", "check-tier2", "check-path-gate-deps") == {
        "check-tier1": [],
        "check-tier2": ["check_path_gate_deps"],
    }


def test_skips_render_in_tier_order_not_config_order() -> None:
    listed = skips(
        "check-tier2", "check-failure-notifier-coverage", "check-path-gate-deps"
    )
    assert listed["check-tier2"] == [
        "check_path_gate_deps",
        "check_failure_notifier_coverage",
    ]


def test_render_writes_the_skip_pairs_into_the_region() -> None:
    text = config_text("check-tier2", "check-path-gate-deps", marked=("check-tier2",))
    out = mod.render_config(text, TIERS)
    assert "          - --skip\n          - check_path_gate_deps\n" in out
    assert "check_stale" not in out
    assert mod.render_config(out, TIERS) == out


def test_an_aggregate_with_skips_and_no_region_raises() -> None:
    text = config_text("check-tier2", "check-path-gate-deps")
    with pytest.raises(ValueError, match="begin marker not found"):
        mod.render_config(text, TIERS)


def test_a_region_whose_skips_went_empty_drops_args_and_its_markers() -> None:
    """Dropping the last explicitly-listed tier member is a legitimate edit, so
    the generator removes the now-empty `args:` key rather than refusing."""
    text = config_text("check-tier2", marked=("check-tier2",))
    out = mod.render_config(text, TIERS)
    assert "args:" not in out
    assert "BEGIN GENERATED" not in out
    assert "END GENERATED" not in out
    assert "- id: check-tier2" in out
    assert mod.render_config(out, TIERS) == out


def test_two_ci_truth_serum_blocks_are_refused() -> None:
    text = config_text("check-tier2") + textwrap.indent(
        textwrap.dedent(
            """\
            - repo: https://github.com/y/ci-truth-serum
              rev: def
              hooks:
                - id: check-tier1
            """
        ),
        "  ",
    )
    with pytest.raises(ValueError, match="exactly one ci-truth-serum"):
        mod.render_config(text, TIERS)


def test_parse_tiers_reads_the_member_names_off_the_registry() -> None:
    source = textwrap.dedent(
        """\
        WORKFLOW = "workflow"
        SHELL = "shell"
        TIERS: dict[str, list[tuple[str, str]]] = {
            "1": [("check_a", WORKFLOW), ("check_b", SHELL)],
            "extras": [("check_c", SHELL)],
        }
        """
    )
    assert mod.parse_tiers(source) == {
        "1": ["check_a", "check_b"],
        "extras": ["check_c"],
    }


def test_parse_tiers_refuses_a_module_with_no_registry() -> None:
    with pytest.raises(ValueError, match="no TIERS"):
        mod.parse_tiers("OTHER = {}\n")


def test_every_committed_skip_names_a_separately_listed_hook() -> None:
    """Half of the invariant the generator maintains, read off the real config.

    A skip whose hook is not listed on its own is coverage lost: nothing runs
    that member. The other half — a listed tier member that is NOT skipped, and
    so runs twice — needs the tier registry, which the generator reads from the
    pinned ci-truth-serum clone rather than from this repo.
    """
    text = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    empty_registry = {key: [] for key in TIERS}
    standalone, skipped = set(), []
    for hook in mod.cts_repo(yaml.safe_load(text))["hooks"]:
        args = hook.get("args") or []
        skipped += [a for i, a in enumerate(args) if i and args[i - 1] == "--skip"]
        if mod.tier_of(hook["id"], empty_registry) is None:
            standalone.add(mod.module_name(hook["id"]))
    assert set(skipped) <= standalone
    assert len(skipped) == len(set(skipped))
    assert skipped, "the config skips nothing, so this test would pass vacuously"
