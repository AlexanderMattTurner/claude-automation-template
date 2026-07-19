"""Runs the node:test suite for .github/scripts/check-flag-arity-guard.mjs.

The checker and its behaviour tests are JavaScript (node builtins only); this
bridge executes them under `node --test` so the pytest CI job enforces them.
node must be present — a missing interpreter is a red, not a silent pass, so the
suite can't go green while the checker is unverified.
"""

import subprocess

from tests._helpers import REPO_ROOT

_TEST = REPO_ROOT / ".github" / "scripts" / "check-flag-arity-guard.test.mjs"


def test_node_suite_passes() -> None:
    proc = subprocess.run(
        ["node", "--test", str(_TEST)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"node --test failed:\n{proc.stdout}\n{proc.stderr}"
