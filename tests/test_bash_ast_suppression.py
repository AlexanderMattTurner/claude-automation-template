"""An annotation covers its site however many annotations stack above it.

covers: .github/scripts/checks/_bash_ast.py
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
)
sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts" / "checks"))

from _bash_ast import parse, suppressed_lines  # noqa: E402


def _exempt(script: str) -> set[int]:
    return suppressed_lines(parse(script), "curl-retry-ok:")


def test_an_annotation_covers_the_line_below_it():
    assert 2 in _exempt("# curl-retry-ok: reason\ncurl -o f https://x\n")


def test_a_trailing_annotation_covers_its_own_line():
    assert 1 in _exempt("curl -o f https://x # curl-retry-ok: reason\n")


def test_a_second_annotation_between_does_not_push_the_site_out_of_range():
    script = "# curl-retry-ok: reason\n# pin-exempt: other\n# echo-fallback-ok: third\ncurl -o f https://x\n"
    assert 4 in _exempt(script)


def test_a_comment_trailing_the_site_does_not_hide_the_site():
    script = "# curl-retry-ok: reason\ncurl -o f https://x # fetch it\n"
    assert 2 in _exempt(script)


def test_a_marker_with_no_reason_exempts_nothing():
    assert _exempt("# curl-retry-ok:\ncurl -o f https://x\n") == set()
