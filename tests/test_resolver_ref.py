"""The external-repository pins: one reader for the resolver, one SHA per repository.

`resolver-ref.py` reads the resolver pin from the `resolve` job's `uses:` line,
which is the one place GitHub requires a literal and Dependabot moves. The last
test walks every pin this tree writes for the three external repositories it
runs code from, and asserts each one names a single commit.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "resolver-ref.py"
CALLER = Path(".github/workflows/auto-resolve-conflicts.yaml")
RESOLVER = "agent-resolve-merge-conflicts"
EXTERNAL_REPOS = (RESOLVER, "agent-review", "ci-truth-serum")

# `owner/name[/path]@<sha>`: a pin that names its repository on the same token.
AT_PIN = re.compile(r"[\w.-]+/(?P<repo>[\w.-]+)[^@\s\"']*@(?P<sha>[0-9a-f]{40})\b")
SHA = re.compile(r"\b[0-9a-f]{40}\b")


def run(root: Path, *args: str, env: dict | None = None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, str(root)],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def caller_with(tmp_path: Path, uses: str) -> Path:
    path = tmp_path / CALLER
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump({"jobs": {"resolve": {"uses": uses}}}), encoding="utf-8"
    )
    return tmp_path


def test_it_prints_the_ref_the_live_uses_line_names():
    uses = yaml.safe_load((REPO_ROOT / CALLER).read_text(encoding="utf-8"))["jobs"][
        "resolve"
    ]["uses"]
    result = run(REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout == uses.partition("@")[2] + "\n"


def test_github_output_carries_the_repository_and_the_ref(tmp_path: Path):
    root = caller_with(
        tmp_path, f"Owner/{RESOLVER}/.github/workflows/auto-resolve.yaml@{'a' * 40}"
    )
    out = tmp_path / "out"
    out.write_text("prior=1\n", encoding="utf-8")
    result = run(root, "--github-output", env={"GITHUB_OUTPUT": str(out)})
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert (
        out.read_text(encoding="utf-8")
        == f"prior=1\nrepository=Owner/{RESOLVER}\nref={'a' * 40}\n"
    )


@pytest.mark.parametrize(
    "uses",
    [
        f"Owner/{RESOLVER}/.github/workflows/auto-resolve.yaml",
        f"Owner/{RESOLVER}/.github/workflows/auto-resolve.yaml@",
        f"Owner/{RESOLVER}/.github/workflows/auto-resolve.yaml@--upload-pack=x",
        "./.github/workflows/auto-resolve.yaml@main",
        f"Owner/{RESOLVER}/.github/workflows/other.yaml@{'a' * 40}",
    ],
    ids=["no-ref", "empty-ref", "option-ref", "local-call", "other-workflow"],
)
def test_it_refuses_a_uses_line_with_no_usable_pin(tmp_path: Path, uses: str):
    result = run(caller_with(tmp_path, uses))
    assert result.returncode != 0
    assert "usable ref" in result.stderr
    assert result.stdout == ""


def _strings(node):
    """Every string scalar under NODE, nested containers included."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _pins_in(value: str, context: str) -> set[tuple[str, str]]:
    """(repo, sha) pairs one string writes for an external repository.

    A pin that names its repository on the same token is attributed to that
    repository. A bare SHA belongs to the external repository the same string
    names, or else the one the enclosing mapping names (an `env:` block that
    holds the repository and the ref as two keys).
    """
    found = set()
    spans = []
    for match in AT_PIN.finditer(value):
        spans.append(match.span("sha"))
        if match["repo"] in EXTERNAL_REPOS:
            found.add((match["repo"], match["sha"]))
    for match in SHA.finditer(value):
        if any(start <= match.start() < end for start, end in spans):
            continue
        named = [r for r in EXTERNAL_REPOS if r in value] or [
            r for r in EXTERNAL_REPOS if r in context
        ]
        found.update((repo, match.group()) for repo in named)
    return found


def _walk(node, found: set[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        context = json.dumps(node)
        for value in node.values():
            if isinstance(value, str):
                found.update(_pins_in(value, context))
            else:
                _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, str):
                found.update(_pins_in(value, value))
            else:
                _walk(value, found)


def external_pins() -> dict[str, set[str]]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", ".github", ".pre-commit-config.yaml"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    found: set[tuple[str, str]] = set()
    for rel in filter(None, tracked):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if rel.endswith((".yaml", ".yml")):
            _walk(yaml.safe_load(text), found)
            continue
        for line in text.splitlines():
            found.update(_pins_in(line, line))
    pins: dict[str, set[str]] = {repo: set() for repo in EXTERNAL_REPOS}
    for repo, sha in found:
        pins[repo].add(sha)
    return pins


@pytest.mark.drift_guard(
    "GitHub reads a `uses:` and pre-commit reads a `rev:` as literals, so the "
    "ci-truth-serum pin is written twice: sync-required-checks runs in repos "
    "whose .pre-commit-config.yaml need not name ci-truth-serum, so it cannot "
    "read that file's rev."
)
def test_each_external_repository_is_pinned_to_one_commit():
    pins = external_pins()
    # Non-vacuity: every repository is pinned somewhere, or the walk found nothing.
    assert all(pins.values()), pins
    assert {repo: len(shas) for repo, shas in pins.items()} == {
        repo: 1 for repo in EXTERNAL_REPOS
    }, pins
    assert pins[RESOLVER] == {run(REPO_ROOT).stdout.strip()}
