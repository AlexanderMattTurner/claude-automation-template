#!/usr/bin/env python3
"""No workflow step downstream of an agent step may reach Python by bare name.

`claude-code-action` writes `/usr/bin` and `/bin` to `$GITHUB_PATH`. The
runner rebuilds `PATH` between steps by PREPENDING each line, so the LAST
writer wins and every step after the agent resolves a bare `python3` to the
system interpreter. A step that ran fine before the agent step then dies on
`ModuleNotFoundError` for a package `uv sync` demonstrably installed, and
nothing in the failure names the action that moved the ground.

Naming the interpreter by path (`"$REPO_ROOT/.venv/bin/python3"`, or an
explicit `PYTHON`) is immune. Scoped to Python: the shadowing binary must
EXIST at the prepended path, and a virtualenv binds its packages to the
interpreter. Node resolves from the importing file's directory and survives.

Opt out on the offending line, or in the comment block above it, with
`# allow-path-shadowed-interpreter: <reason>`.

Simplified from the source check this was ported from: this version reads
only each workflow's own inline `run:` steps in job-declaration order — it
does not expand local composite actions, and it does not follow a `run:`
step's own `.github/scripts/*.sh` reference into that script's body. Both are
false-negative directions: a shadowed interpreter reached only through a
composite action or a followed script is not seen.
"""

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _linecheck import (  # noqa: E402  # pylint: disable=wrong-import-position
    strip_comment,
)
from _ratchet import REPO_ROOT  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ACTION = "anthropics/claude-code-action"
_BARE_WORD = re.compile(r"(?<![\w/.-])python3?(?![\w.-])")
_BARE_DEFAULT = re.compile(r":-\s*python3?(?![\w.-])")
_ALLOW = "# allow-path-shadowed-interpreter:"


def _annotated(raw: list[str], number: int) -> bool:
    index = number - 1
    while index >= 0 and (index == number - 1 or raw[index].lstrip().startswith("#")):
        if _ALLOW in raw[index]:
            return True
        index -= 1
    return False


def _bare_python(source: str) -> list[tuple[int, str]]:
    """(1-based line, its text) for every unannotated bare Python name."""
    raw = source.split("\n")
    hits = []
    for number, line in enumerate(raw, start=1):
        code = strip_comment(line)
        if (_BARE_WORD.search(code) or _BARE_DEFAULT.search(code)) and not _annotated(
            raw, number
        ):
            hits.append((number, line.strip()))
    return hits


def violations(root: Path = REPO_ROOT) -> dict[tuple[str, int], str]:
    """{(workflow, line): code} for every bare-Python site downstream of an
    agent step, in execution order within each job."""
    out: dict[tuple[str, int], str] = {}
    for workflow in sorted((root / ".github/workflows").glob("*.y*ml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
        for job in (data.get("jobs") or {}).values():
            downstream = False
            for step in job.get("steps") or []:
                if downstream and step.get("run"):
                    for line, code in _bare_python(step["run"]):
                        out[(workflow.name, line)] = code
                downstream = downstream or _AGENT_ACTION in (step.get("uses") or "")
    return out


def main() -> None:
    found = violations()
    if not found:
        return
    print(
        "Bare `python3`/`python` downstream of an agent step, whose PATH write "
        "shadows it with the system interpreter:\n"
    )
    for (name, line), code in sorted(found.items()):
        print(f"  {name}:{line}: {code}")
    print(
        '\nName the interpreter by path ("$REPO_ROOT/.venv/bin/python3", or an '
        f"explicit PYTHON), or annotate the line `{_ALLOW} <reason>`."
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
