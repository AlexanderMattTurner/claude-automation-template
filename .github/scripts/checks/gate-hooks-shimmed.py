#!/usr/bin/env python3
"""Require every hook in settings to run behind a resilient launcher.

A hook must be an ARGUMENT to ``safe-launch.sh``, never the first hook binary
the command runs. The launcher parse-checks the hook first: a hook that fails
to PARSE exits non-zero, which Claude Code treats as non-blocking, letting the
tool call through UNGUARDED. The launcher converts that to fail-closed instead.

PROBLEM CLASS — the rule reads every hook as a gate rather than checking a list
of gate names, because a hand-listed set omits the gate added after it was
written, and the omission is silent. An advisory hook that emits no verdict
opts out through ``UNWRAPPED_OK`` below, which costs a reason someone reads.

Usage: ``gate-hooks-shimmed.py [settings.json ...]``. With no argv it checks
``.claude/settings.json`` and ``.claude/settings.local.json``, whichever exist.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ratchet import REPO_ROOT  # noqa: E402  # pylint: disable=wrong-import-position

DEFAULT_FILES = (".claude/settings.json", ".claude/settings.local.json")

LAUNCHERS = ("safe-launch.sh",)

# Hooks a settings command may run with no launcher ahead of them. Each entry
# says why a parse failure in that hook changes no verdict Claude Code acts on.
UNWRAPPED_OK = {
    "session-setup.sh": (
        "SessionStart provisioning that emits no verdict, and the shell "
        "running it reports its own parse error"
    ),
    "parallelism-nudge.mjs": (
        "PostToolUse advisory that only prints a nudge as additionalContext, "
        "so a parse failure loses the nudge and guards nothing"
    ),
    "drop-superseded-ci-events.mjs": (
        "UserPromptSubmit filter that only drops stale event text, gating no tool call"
    ),
}

_SCRIPT = re.compile(r"[\w.-]+\.(?:mjs|bash|sh)")


def _hooks_in(text: str) -> list[str]:
    """The hook basenames TEXT names, in the order they appear. A launcher is
    not a hook, and neither is an opted-out advisory hook."""
    return [
        name
        for name in _SCRIPT.findall(text)
        if name not in LAUNCHERS and name not in UNWRAPPED_OK
    ]


def raw_gate_invocations(command: str) -> list[str]:
    """Hook basenames COMMAND names with no launcher naming them ahead of it
    in the same command string.

    A settings ``command`` here is a fixed template (no untrusted input), so a
    left-to-right substring scan over the raw text is sound: a launcher token
    that appears before a gate token protects everything after it, matching
    how ``safe-launch.sh`` execs its one argument.
    """
    launcher_positions = [
        m.end()
        for launcher in LAUNCHERS
        for m in re.finditer(re.escape(launcher), command)
    ]
    earliest_launcher = min(launcher_positions, default=None)
    hits: list[str] = []
    for match in _SCRIPT.finditer(command):
        name = match.group(0)
        if name in LAUNCHERS or name in UNWRAPPED_OK:
            continue
        if earliest_launcher is not None and match.start() > earliest_launcher:
            continue
        hits.append(name)
    return hits


def unshimmed_gates(settings: dict) -> list[str]:
    """Locators for every settings command that invokes a gate without a
    launcher."""
    violations: list[str] = []
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return violations
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for i, group in enumerate(groups):
            entries = group.get("hooks", []) if isinstance(group, dict) else []
            if not isinstance(entries, list):
                continue
            for j, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if not isinstance(command, str):
                    continue
                for gate in raw_gate_invocations(command):
                    violations.append(f"{event}[{i}].hooks[{j}]: raw {gate}")
    return violations


def check_file(path: Path) -> list[str]:
    settings = json.loads(path.read_text(encoding="utf-8"))
    return unshimmed_gates(settings)


_REMEDY = (
    "route the command through safe-launch.sh, as the other hook entries in "
    "the same settings file do — or, when the hook emits no verdict, add it "
    "to UNWRAPPED_OK in .github/scripts/checks/gate-hooks-shimmed.py with the "
    "reason."
)


def _default_paths() -> list[Path]:
    """The settings files to read when the caller named none.

    This refusal is what stops the check passing vacuously: with no settings
    file present it would iterate an empty list and report every hook shimmed
    having read nothing, so a renamed or deleted `.claude/settings.json` would
    disarm the gate silently.
    """
    paths = [REPO_ROOT / f for f in DEFAULT_FILES if (REPO_ROOT / f).exists()]
    if not paths:
        raise SystemExit(
            "gate-hooks-shimmed: none of "
            + ", ".join(DEFAULT_FILES)
            + f" exist under {REPO_ROOT} — refusing to report a hook surface "
            "this run never read."
        )
    return paths


def main(argv: list[str]) -> None:
    paths = [Path(a) for a in argv] if argv else _default_paths()
    status = 0
    for path in paths:
        for loc in check_file(path):
            print(
                f"{path}: {loc} invoked without safe-launch.sh — a gate that "
                f"fails to parse is non-blocking, so it fails OPEN. {_REMEDY}",
                file=sys.stderr,
            )
            status = 1
    sys.exit(status)


if __name__ == "__main__":
    main(sys.argv[1:])
