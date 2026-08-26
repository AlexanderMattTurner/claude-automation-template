"""Every `*.test.mjs` in the tree must be matched by the `pnpm test` runner.

`node --test` takes an explicit list of glob patterns, and a suite that no
pattern reaches is simply never loaded — the run reports a healthy pass count
while that module is unverified. Nothing else catches it: the file exists, its
assertions are real, and reading either the suite or `package.json` alone shows
nothing wrong. It is a silent single-token omission (a glob rooted at one
directory, a suite added under another), and adding a directory makes it easy to
reintroduce.

So this iterates the single source — the test files that actually exist — and
asserts the second copy (the runner's pattern list) reaches each one, rather
than naming any suite. A new suite anywhere is covered with no edit here.
"""

import json
import shlex
import subprocess
from glob import glob

from tests._helpers import REPO_ROOT

PACKAGE_JSON = REPO_ROOT / "package.json"


def _runner_patterns() -> list[str]:
    """The glob patterns `pnpm test` hands to `node --test`.

    Fails loudly when the command no longer contains a `node --test` invocation:
    a restructured script must re-derive this, never silently match nothing and
    let the assertion below pass vacuously.
    """
    test_script = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["scripts"][
        "test"
    ]
    tokens = shlex.split(test_script)
    assert "--test" in tokens, (
        f"package.json scripts.test no longer invokes `node --test`: {test_script!r}. "
        "This guard derives the runner's file set from that flag — re-point it."
    )
    patterns = [
        t for t in tokens[tokens.index("--test") + 1 :] if not t.startswith("-")
    ]
    assert patterns, (
        f"`node --test` in scripts.test has no path/glob arguments: {test_script!r}. "
        "Bare `node --test` uses Node's own discovery, which this guard cannot model."
    )
    return patterns


def _tracked_js_test_files() -> set[str]:
    """Repo-relative paths of every tracked `*.test.mjs`.

    Reads git's index rather than walking the filesystem so an untracked scratch
    file in a working tree can never fail the suite.
    """
    out = subprocess.run(
        ["git", "ls-files", "*.test.mjs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line for line in out.splitlines() if line}


def test_every_js_test_file_is_reached_by_the_runner() -> None:
    tracked = _tracked_js_test_files()
    assert tracked, (
        "No tracked *.test.mjs files found — this guard would pass vacuously. "
        "Check the git ls-files pattern."
    )

    covered: set[str] = set()
    for pattern in _runner_patterns():
        covered.update(glob(pattern, root_dir=REPO_ROOT, recursive=True))

    unreached = tracked - covered
    assert not unreached, (
        "These test suites exist but no `node --test` pattern in package.json "
        f"reaches them, so `pnpm test` never loads them: {sorted(unreached)}. "
        "Add a covering glob to scripts.test."
    )
