#!/usr/bin/env python3
"""Ban `git` remote operations in runtime shell tooling from running with no timeout.

A `git` call to a remote — ls-remote / fetch / clone / push / pull — carries no
time bound of its own: a wedged endpoint hangs the call *forever*. In an
interactive hook, a teardown, or a poll loop, that stall eats the user's Ctrl-C
or strands a cleanup pass.

The safe form places a bound FIRST, so `git` becomes an argument, not the
command word: `timeout … git fetch`. This lint fires only when `git` is the
command name of a simple command (past any wrapper that already bounds it) and
its subcommand is a literal remote verb. A dynamic subcommand (`git "$@"`) is
not a literal verb, so a deliberate unbounded fallback is exempt.

Scope: `.claude/hooks/` and `.hooks/` — the runtime tooling a live session and
its teardown run. `.github/scripts` is out of scope, since a CI job carries a
workflow-level `timeout-minutes` backstop.

Opt a `git` call that genuinely must block out with
`# allow-unbounded: <reason>` on the command's line or the line above.

Simplified from the source check this was ported from: it reads only the
literal words the parser resolves, so `git "$sub"` (a variable subcommand) is
never flagged even when it always evaluates to a remote verb.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _bash_ast import (  # noqa: E402  # pylint: disable=wrong-import-position
    command_words,
    parse,
    suppressed_lines,
    walk,
)
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ALLOW = "allow-unbounded:"

# git subcommands that talk to a remote — the ones that hang on an
# unresponsive endpoint. Local subcommands never wedge and are absent.
_REMOTE_SUBCOMMANDS = frozenset({"ls-remote", "fetch", "clone", "push", "pull"})

# git global options that sit before the subcommand and consume the following
# token as their value, so the subcommand is not the token right after `git`.
_VALUE_OPTS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)


def _subcommand(args: list[str | None]) -> str | None:
    """The first token in ARGS that is not a global option or a global
    option's value, or None when that token cannot be read literally."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg is None:
            return None
        if arg in _VALUE_OPTS:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return arg
    return None


def violations(text: str) -> list[int]:
    """1-based line numbers where a bare remote `git` runs with no timeout."""
    root = parse(text)
    exempt = suppressed_lines(root, _ALLOW)
    hits: list[int] = []
    for node in walk(root):
        words = command_words(node)
        if not words or words[0] != "git":
            continue
        if _subcommand(words[1:]) not in _REMOTE_SUBCOMMANDS:
            continue
        line = node.start_point[0] + 1
        if line not in exempt:
            hits.append(line)
    return sorted(hits)


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "remote `git` runs with no timeout — a wedged or unresponsive "
            "endpoint hangs the tool forever. Put a bound first "
            "(`timeout … git <cmd>`), or annotate `# allow-unbounded: <reason>`.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
