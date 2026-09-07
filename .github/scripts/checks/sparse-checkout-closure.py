#!/usr/bin/env python3
"""Every sparse-checkout job's list must cover the paths its own steps read.

PROBLEM CLASS — a hand-maintained ``sparse-checkout:`` list vs. the job's real
file dependencies. A job that checks out sparsely and then sources or invokes
a tracked file the list omits dies with "No such file or directory" on its
first reference, and nothing static sees it coming: sparse-checkout sets
SKIP_WORKTREE on the excluded entries rather than removing them, so
``git ls-files`` and every other check still see a complete tree — only the
runner disagrees.

This derives each job's dependency set with a regex sweep over the ``run:``
text of the steps that execute after the checkout (its "window": from the
checkout to the job's next full re-checkout, or the job's end) — a repo-path
token (``bash .github/scripts/x.sh``, ``source lib/y.bash``, a bare
``.github/...``-rooted path) is a dependency the job's own tree must contain.
That is a coarser derivation than a real execution-closure walk (it cannot
follow an import graph), so it only ever WIDENS the floor a hand-written list
must clear, never narrows it — a script it can't see stays hand-declared.

Opt out on the workflow with ``# sparse-checkout-ok: <dep> <reason>`` anywhere
in the file — one comment excuses that dependency for every checkout in it.
"""

import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _ratchet import (  # noqa: E402  # pylint: disable=wrong-import-position
    REPO_ROOT,
    tracked_like_files,
)

JsonObject = dict[str, Any]

_UNMODELLED = ("*", "?", "[", "!")

# A repo-relative path a `run:` step could reference: rooted at a known
# top-level dir, ending in a known source/extensionless-shebang suffix.
_PATH_TOKEN = re.compile(
    r"(?<![\w./])"
    r"(?:\.github|\.claude|\.hooks|config|scripts|resolver|tests)"
    r"(?:/[\w.-]+)+"
)


def _is_checkout(step: JsonObject) -> bool:
    return str(step.get("uses") or "").startswith("actions/checkout")


def _window(steps: list[JsonObject], start: int) -> list[JsonObject]:
    """Steps that run against this checkout's tree: from START onward, up to
    the job's next full (no ``path:``) checkout, or the job's end."""
    window = []
    for step in steps[start:]:
        if _is_checkout(step) and not (step.get("with") or {}).get("path"):
            break
        window.append(step)
    return window


class Checkout:
    def __init__(
        self,
        workflow: Path,
        job_name: str,
        window: list[JsonObject],
        patterns: tuple[str, ...],
        cone: bool,
    ) -> None:
        self.workflow = workflow
        self.job_name = job_name
        self.window = window
        self.patterns = patterns
        self.cone = cone

    @property
    def where(self) -> str:
        return f"{self.workflow.name}:{self.job_name}"


def checkouts(workflow: Path) -> list[Checkout]:
    """Every job in WORKFLOW that checks out sparsely with a literal pattern
    list. A `${{ }}` value or a wildcard pattern is decided/matched at a level
    this derivation does not model, so that job is skipped rather than judged
    against a guess."""
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return []
    found = []
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
        for index, step in enumerate(steps):
            if not _is_checkout(step):
                continue
            with_inputs = step.get("with") or {}
            raw = with_inputs.get("sparse-checkout")
            if not isinstance(raw, str) or not raw.strip() or "${{" in raw:
                continue
            if any(ch in raw for ch in _UNMODELLED):
                continue
            cone = (
                str(with_inputs.get("sparse-checkout-cone-mode", True)).lower()
                != "false"
            )
            window = _window(steps, index + 1)
            found.append(Checkout(workflow, job_name, window, tuple(raw.split()), cone))
    return found


def _covers(checkout: Checkout, path: str) -> bool:
    """Whether CHECKOUT's own sparse-checkout list includes PATH.

    Cone mode anchors a slash-less pattern to the repo root; every listed
    pattern here matters only as a directory prefix, since the target's real
    ``sparse-checkout:`` lists carry no bare-filename or wildcard entries (both
    are excluded above via ``_UNMODELLED``). Non-cone mode is treated the same
    way: a listed entry covers PATH when PATH is that entry or sits under it.
    """
    return any(
        path == pattern.rstrip("/") or path.startswith(f"{pattern.rstrip('/')}/")
        for pattern in checkout.patterns
    )


def _exemptions(workflow: Path) -> set[str]:
    text = workflow.read_text(encoding="utf-8")
    out = set()
    for match in re.finditer(r"#\s*sparse-checkout-ok:\s*(?P<path>\S+)", text):
        out.add(match.group("path"))
    return out


def _dependencies(window: list[JsonObject]) -> set[str]:
    deps: set[str] = set()
    for step in window:
        run = step.get("run")
        if isinstance(run, str):
            deps |= set(_PATH_TOKEN.findall(run))
        uses = step.get("uses")
        if isinstance(uses, str) and uses.startswith("./"):
            deps.add(uses[2:].split("@")[0].rstrip("/"))
    return deps


def _tracked(dep: str, files: frozenset[str]) -> bool:
    """A dependency exists when it is a tracked file OR a tracked directory.
    `uses: ./.github/actions/x` names a directory, so an exact-file test alone
    discards it and the job's sparse-checkout hole goes unreported."""
    return dep in files or any(rel.startswith(f"{dep}/") for rel in files)


def uncovered(checkout: Checkout, files: frozenset[str], exempt: set[str]) -> list[str]:
    deps = _dependencies(checkout.window)
    return sorted(
        dep
        for dep in deps
        if _tracked(dep, files) and dep not in exempt and not _covers(checkout, dep)
    )


def main(root: Path = REPO_ROOT) -> None:
    workflow_dir = root / ".github" / "workflows"
    workflows = sorted(workflow_dir.glob("*.y*ml"))
    if not workflows:
        raise SystemExit(
            f"sparse-checkout-closure: no workflows found in {workflow_dir}"
        )
    files = frozenset(tracked_like_files(root))
    holes = 0
    for workflow in workflows:
        found = checkouts(workflow)
        if not found:
            continue
        exempt = _exemptions(workflow)
        for checkout in found:
            missing = uncovered(checkout, files, exempt)
            if not missing:
                continue
            print(f"{checkout.where}: {' '.join(missing)}")
            holes += 1
    if holes:
        raise SystemExit(
            f"{holes} sparse-checkout job(s) miss files their own steps read — a "
            'step that sources or invokes one dies with "No such file or '
            "directory\" the first time that code path runs. Widen the job's "
            "sparse-checkout list, or add a `# sparse-checkout-ok: <dep> <reason>` "
            "comment."
        )


if __name__ == "__main__":
    main()
