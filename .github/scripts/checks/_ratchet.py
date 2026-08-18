"""Shared grandfathered-baseline ratchet for the checks under this directory.

PROBLEM CLASS — `.github/CLAUDE.md`'s "Lint ratchets" section: a lint disabled
by its own existing violations must grandfather them, not go silent, and a
baseline entry that outlives its violation must fail so the list only shrinks.
Every ratcheted check here (`file-size`, `comment-block-length`,
`unspecified-encoding`) imports this instead of re-deriving the rule.

A file's METRIC (a line count, a violation count) may exceed CAP only when a
baseline entry names its exact value at baseline time. Growing past that
value is a new violation; shrinking below it is a stale entry the author must
lower with `--write-baseline`. A metric at or under cap with a lingering
baseline entry is stale too, and — in a COMPLETE (whole-tree) scan — so is a
baseline entry whose file no longer exists.

PROBLEM CLASS — "which files does a whole-tree check read": `tracked_like_files`
below is this directory's one answer, ratcheted check or not. A second walk
drifts on what it prunes, and a walk that prunes every dot-directory reads none
of `.github`, `.claude` or `.hooks` — a check that reports success having read
nothing. The scan root itself comes from `.github/scripts/_repo_root.py`.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _repo_root import repo_root  # noqa: E402  # pylint: disable=wrong-import-position

REPO_ROOT = repo_root(Path(__file__))

# VCS, dependency and cache directories: nothing under them is tracked, so no
# check here has an opinion about their contents. Dot-directories that DO hold
# tracked source (`.github`, `.claude`, `.hooks`) are deliberately absent.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        ".ruff_cache",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".cache",
    }
)


class BaselineError(Exception):
    """The baseline file is missing, unreadable, or not a well-formed ratchet policy."""


def tracked_like_files(root: Path) -> list[str]:
    """Repo-relative paths under ROOT, skipping VCS/dependency/cache dirs —
    the `find`-driven stand-in for `git ls-files` a ratcheted check walks
    instead of shelling out to git."""
    found: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_dir():
                if entry.name in _SKIP_DIRS:
                    continue
                stack.append(entry)
            elif entry.is_file():
                found.append(str(entry.relative_to(root)))
    return found


def load_policy(path: Path) -> dict:
    """The parsed `{"cap": int, "baseline": {rel: count}}` ratchet policy at
    PATH. Raises BaselineError on anything short of a well-formed file — a
    missing or broken baseline must never read as "no violations"."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BaselineError(f"{path}: cannot read baseline file — {exc}") from exc
    try:
        policy = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{path}: baseline is not valid JSON — {exc}") from exc
    if not isinstance(policy, dict) or "cap" not in policy or "baseline" not in policy:
        raise BaselineError(
            f"{path}: baseline must be an object with 'cap' and 'baseline' keys"
        )
    return policy


def findings(
    counts: dict[str, int],
    policy: dict,
    unit: str,
    *,
    cap_desc: str | None = None,
    complete: bool = True,
) -> list[str]:
    """Ratchet violations over COUNTS ({rel: metric}) against POLICY. UNIT
    names the metric in the message ("lines", "violations"); CAP_DESC is how
    the cap itself reads ("100-line", "0" if omitted — `str(cap)`). COMPLETE
    marks COUNTS as covering every in-scope file (a whole-tree scan): only
    then can a baseline entry with no matching key be trusted as "file gone"
    rather than "just not in this partial scan"."""
    cap = policy["cap"]
    cap_desc = str(cap) if cap_desc is None else cap_desc
    baseline = policy["baseline"]
    out = []
    for rel, count in sorted(counts.items()):
        baselined = baseline.get(rel)
        if count > cap:
            if baselined is None:
                out.append(f"{rel}: {count} {unit} exceeds the {cap_desc} cap (new).")
            elif count > baselined:
                out.append(
                    f"{rel}: {count} {unit} grew past its baseline of {baselined}."
                )
            elif count < baselined:
                out.append(
                    f"{rel}: {count} {unit} shrank below its baseline of {baselined} "
                    "— stale entry, regenerate with --write-baseline."
                )
        elif baselined is not None:
            out.append(
                f"{rel}: {count} {unit} is at/under the {cap_desc} cap — baseline "
                "entry is stale, regenerate with --write-baseline."
            )
    if complete:
        for rel in sorted(set(baseline) - set(counts)):
            out.append(
                f"{rel}: baseline entry has no matching file — stale entry, "
                "regenerate with --write-baseline."
            )
    return out


def write_baseline(path: Path, policy: dict, counts: dict[str, int]) -> None:
    """Overwrite POLICY's baseline at PATH with every file whose COUNTS metric
    exceeds cap — the only edit that can make `findings` above report clean."""
    cap = policy["cap"]
    policy["baseline"] = {rel: n for rel, n in counts.items() if n > cap}
    path.write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
