"""The splice into a generated region refuses a document it cannot rewrite whole.

covers: scripts/lib_marked_region.py
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
)
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lib_marked_region import (  # noqa: E402  (path inserted just above)
    region_begin,
    region_end,
    splice,
)

BEGIN = region_begin("skips", "gen.py")
END = region_end("skips")


def _doc(pairs: int) -> str:
    body = "".join(f"{BEGIN}\nstale\n{END}\n" for _ in range(pairs))
    return f"head\n{body}tail\n"


def test_one_pair_is_rewritten_in_place():
    assert splice(_doc(1), begin=BEGIN, end=END, block="fresh", label="cts") == (
        f"head\n{BEGIN}\nfresh\n{END}\ntail\n"
    )


@pytest.mark.parametrize(
    ("which", "doc"),
    [
        ("begin", f"{BEGIN}\nstale\n{BEGIN}\nstale\n{END}\n"),
        ("end", f"{BEGIN}\nstale\n{END}\n{END}\n"),
    ],
)
def test_a_duplicated_marker_is_refused_rather_than_half_rewritten(which, doc):
    with pytest.raises(ValueError, match=f"cts: {which} marker appears 2 times"):
        splice(doc, begin=BEGIN, end=END, block="fresh", label="cts")


def test_a_missing_begin_marker_is_refused():
    with pytest.raises(ValueError, match="cts: begin marker not found"):
        splice("head\ntail\n", begin=BEGIN, end=END, block="fresh", label="cts")


def test_an_end_marker_before_its_begin_is_refused():
    doc = f"{END}\n{BEGIN}\n"
    with pytest.raises(ValueError, match="cts: no end marker after the begin marker"):
        splice(doc, begin=BEGIN, end=END, block="fresh", label="cts")
