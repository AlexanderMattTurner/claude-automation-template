#!/usr/bin/env python3
"""Validate that Claude Code ``SKILL.md`` files carry the frontmatter a skill needs.

Based on analysis of common skills failures:
https://cashandcache.substack.com/p/i-analyzed-40-claude-skills-failures

Checks enforced:

1. YAML frontmatter present (the file starts with ``---``).
2. A closing ``---`` delimiter.
3. A ``name:`` key in the frontmatter.
4. A ``description:`` key in the frontmatter.
5. The description reads as 2+ sentences, so the model has activation context.
6. An ``## Examples`` section in the body — a WARNING, not an error.

Skills must use the directory format ``.claude/skills/<name>/SKILL.md``; a flat
``.claude/skills/<name>.md`` is rejected.

The frontmatter is parsed with ``yaml.safe_load``, never scanned as text. The
shell predecessor answered "where does the ``description`` value end?" with
``sed -n '/^description:/,/^[a-z]/p'``, which swallowed the NEXT key: a
``version: 1.2.3`` line after a one-word description supplied the two periods the
sentence check was looking for, and the check passed a description with no
sentences at all. Frontmatter that is not valid YAML at all (``name: "unterminated``)
also passed, because every ``grep`` the shell ran still matched. A real parser has
neither failure mode, reads folded (``description: >``) and block scalars for free,
and treats a ``#`` inside a quoted value as content rather than as the comment
``grep -v '^#'`` took it for.

Usage: ``lint-skills.py [files...]``. Exits 1 when any file has an error.
"""

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # Specific, actionable recovery: a bare traceback in a git
    # hook does not tell the author which command repairs it.
    sys.exit(
        "lint-skills: PyYAML is missing — the SKILL.md frontmatter cannot be "
        "parsed. Install it with `uv sync --extra dev` (or `pip install pyyaml`) "
        "and retry."
    )

FRONTMATTER_DELIMITER = "---"
MIN_PERIODS = 2


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return ``(frontmatter, body)``; frontmatter is ``None`` when unterminated.

    The caller has already established that the first line is the opening
    delimiter, so only the CLOSING one is searched for here.
    """
    lines = text.split("\n")
    for i, line in enumerate(lines[1:], start=1):
        if line == FRONTMATTER_DELIMITER:
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return None, ""


def count_periods(description: str) -> int:
    """Periods in a description — the shell predecessor's proxy for sentence count.

    The proxy and its threshold are preserved verbatim; the fix changes only WHAT
    is counted — the parsed ``description`` value alone, never a neighbouring key.
    """
    return description.count(".")


def check_file(path: str) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for one path. Non-skill paths yield neither."""
    if ".claude/skills/" not in path:
        return [], []

    parts = Path(path)
    # A flat `.claude/skills/<name>.md` has `skills` as its direct parent.
    if parts.parent.name == "skills" and parts.suffix == ".md":
        return [
            f"ERROR: {path} uses flat file format — convert to "
            f".claude/skills/{parts.stem}/SKILL.md"
        ], []

    # Only SKILL.md entrypoints are validated; supporting files are skipped.
    if parts.parent.parent.name != "skills" or parts.name != "SKILL.md":
        return [], []

    text = Path(path).read_text(encoding="utf-8")
    if text.split("\n", 1)[0] != FRONTMATTER_DELIMITER:
        return [f"ERROR: {path} missing YAML frontmatter (must start with ---)"], []

    raw, body = split_frontmatter(text)
    if raw is None:
        return [f"ERROR: {path} missing closing '---' YAML frontmatter delimiter"], []

    try:
        front = yaml.safe_load(raw)
    except yaml.YAMLError as err:
        return [f"ERROR: {path} frontmatter is not valid YAML: {err}"], []
    if not isinstance(front, dict):
        return [f"ERROR: {path} frontmatter is not a YAML mapping"], []

    errors = []
    if "name" not in front:
        errors.append(f"ERROR: {path} missing 'name:' in frontmatter")

    description = front.get("description")
    if description is None:
        errors.append(f"ERROR: {path} missing 'description:' in frontmatter")
    elif count_periods(str(description)) < MIN_PERIODS:
        errors.append(
            f"ERROR: {path} description too short — use 2-3 sentences with "
            "specific activation triggers"
        )

    warnings = []
    if not any(line.startswith("## Examples") for line in body.split("\n")):
        warnings.append(
            f"WARN: {path} missing '## Examples' section — consider adding 2-3 "
            "real input/output examples"
        )
    return errors, warnings


def main(argv: list[str]) -> int:
    failed = False
    for path in argv:
        errors, warnings = check_file(path)
        for line in [*errors, *warnings]:
            print(line, file=sys.stderr)
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
