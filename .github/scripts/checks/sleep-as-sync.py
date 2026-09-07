#!/usr/bin/env python3
"""Ban a fixed `sleep` used to synchronise with an event before an assertion.

A `time.sleep(N)` before an assertion is a bet that the event the test is
waiting for lands inside N seconds. The bet is settled by the scheduler, not by
the code under test, so it loses on a loaded shard. It also loses SILENTLY the
other way: when the event never arrives at all, a sleep long enough to hide the
bug produces a green run.

The fix is to poll the CONDITION with a deadline instead of guessing how long
it takes.

A VIOLATION is a bare ``sleep(...)`` statement — ``time.sleep`` or any
``sleep`` — followed by an assertion LATER IN THE SAME BLOCK. Both parts
matter: a sleep with no assertion after it is pacing, not synchronisation. An
assertion is ``assert``, ``pytest.raises``, or an ``assert_*`` helper.

NOT flagged:
  * a sleep inside a ``while``/``for`` body, which IS the bounded poll this
    lint steers toward;
  * a sleep whose interval is neither a literal nor a module-level numeric
    constant — a caller-supplied or computed interval is a parameter of the
    helper, not a fixed bet made here. A module constant IS flagged:
    ``sleep(_SETTLE)`` is the same fixed bet under another name;
  * a fake-timer call (``freeze_time``, ``useFakeTimers``, ``clock.tick``),
    where no real time passes and there is no race to lose — this lint does
    not special-case those names, since none appear as a Python `sleep` call.

A test whose SUBJECT IS a timeout — where the sleep is the input rather than
the synchronisation — takes ``# allow-sleep: <reason>`` on the sleep's own
line or the line above. The reason is required.
"""

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ANNOTATION_RE = re.compile(r"#\s*allow-sleep:\s*\S")

# Fields that hold a statement list, so each is a BLOCK whose ordering decides
# whether an assertion comes after a sleep.
_BLOCK_FIELDS = ("body", "orelse", "finalbody")


def _numeric_constants(tree: ast.Module) -> set[str]:
    """Module-level names bound to a number, so `sleep(_SETTLE)` reads as the
    fixed interval it is rather than as a caller-supplied one."""
    names = set()
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if isinstance(target, ast.Name) and _is_number(statement.value):
            names.add(target.id)
    return names


def _is_number(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float))


def _is_sleep_call(node: ast.expr, constants: set[str]) -> bool:
    """True when NODE is a `sleep(...)` call with a FIXED number of seconds."""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    callee = node.func
    name = (
        callee.attr
        if isinstance(callee, ast.Attribute)
        else (callee.id if isinstance(callee, ast.Name) else None)
    )
    if name != "sleep":
        return False
    seconds = node.args[0]
    if isinstance(seconds, ast.Name):
        return seconds.id in constants
    return _is_number(seconds)


def _asserts(statements: list[ast.stmt]) -> bool:
    """True when any of STATEMENTS contains an assertion — `pytest.raises` and
    an `assert_*` helper both count, since each loses the same race."""
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Assert):
                return True
            if isinstance(node, (ast.Name, ast.Attribute)):
                label = node.id if isinstance(node, ast.Name) else node.attr
                if label == "raises" or label.startswith("assert"):
                    return True
    return False


def _sleep_lines_in_block(statements: list[ast.stmt], constants: set[str]) -> list[int]:
    hits = []
    for index, statement in enumerate(statements):
        if (
            isinstance(statement, ast.Expr)
            and _is_sleep_call(statement.value, constants)
            and _asserts(statements[index + 1 :])
        ):
            hits.append(statement.lineno)
    return hits


def violations(text: str) -> list[int]:
    """1-based lines of every unexempted sleep-before-assertion in one module."""
    tree = ast.parse(text)
    lines = text.splitlines()
    constants = _numeric_constants(tree)
    hits: set[int] = set()

    for node in ast.walk(tree):
        # A loop body is the bounded poll, so its sleep is the mechanism rather
        # than the defect. Skipping the node's own blocks (not its whole
        # subtree) keeps a sleep in a nested non-loop block reportable.
        if isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
            continue
        for field in _BLOCK_FIELDS:
            block = getattr(node, field, None)
            if isinstance(block, list):
                hits.update(_sleep_lines_in_block(block, constants))

    def exempt(lineno: int) -> bool:
        return any(
            _ANNOTATION_RE.search(lines[n - 1])
            for n in (max(lineno - 1, 1), lineno)
            if n <= len(lines)
        )

    return sorted(n for n in hits if not exempt(n))


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "a fixed sleep is synchronising this assertion — the scheduler "
            "settles the bet, so it flakes under load and passes silently when "
            "the event never arrives. Poll the condition with a deadline "
            "instead, or annotate `# allow-sleep: <reason>` when the sleep IS "
            "the test's subject.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
