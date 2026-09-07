#!/usr/bin/env python3
"""Require a retry on file-writing ``curl`` downloads in this repo's shell.

A single-shot ``curl … -o <file>`` has no resilience to a transient network
blip: on a flaky link or a rate-limited shared-cloud IP it fails the whole
install for one dropped packet. This flags an invocation that runs ``curl``
and writes to a file (``-o``/``--output``) without a ``--retry`` flag and not
wrapped in this repo's retry helper (``retry``/``retry_stdout``,
``.github/scripts/lib-ci-retry.sh``).

Destinations that cannot hold a partial download are out of scope: ``-``
(stdout, captured into a variable) and ``/dev/null`` (a discard, so the
transfer is a measurement, not a download).

A site that must stay single-shot opts out with a
``# curl-retry-ok: <reason>`` on the command's line or the line above.

Simplified from the source check this was ported from: it reads only the
command node the parser sees, so a `curl` invoked through an unrecognized
wrapper or a name built from a variable is not caught, and a `--retry` on a
different curl invocation in the same script does not count for this one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _bash_ast import (  # noqa: E402  # pylint: disable=wrong-import-position
    command_words,
    parse,
    suppressed_lines,
    walk,
)
from _linecheck import run_line_checks  # noqa: E402  # pylint: disable=wrong-import-position

_ALLOW = "curl-retry-ok:"

_NO_FILE_DESTINATIONS = frozenset({"-", "/dev/null"})
_RETRY_WRAPPERS = frozenset({"retry", "retry_stdout"})


def _output_flag(word: str) -> bool:
    """True when WORD is curl's ``-o``/``--output`` flag, including a bundled
    short-flag tail (`-fsSLo` == `-f -s -S -L -o`); the `o` must be the flag
    cluster's LAST letter, so `--connect-timeout` is not mistaken for it."""
    if word == "--output":
        return True
    return (
        word.startswith("-")
        and not word.startswith("--")
        and word[1:].isalpha()
        and word.endswith("o")
    )


def _writes_a_file(words: list[str | None]) -> bool:
    for i, word in enumerate(words):
        if word is None:
            continue
        if word.startswith("--output="):
            if word.removeprefix("--output=") not in _NO_FILE_DESTINATIONS:
                return True
        elif _output_flag(word):
            destination = words[i + 1] if i + 1 < len(words) else ""
            if destination not in _NO_FILE_DESTINATIONS:
                return True
    return False


def _unretried_download(words: list[str | None]) -> bool:
    if "curl" not in words:
        return False
    if not _writes_a_file(words):
        return False
    string_words = {w for w in words if w is not None}
    if _RETRY_WRAPPERS & string_words:
        return False
    return not any(w and w.startswith("--retry") for w in words)


def violations(text: str) -> list[int]:
    """1-based line numbers running a file-writing ``curl`` with no retry."""
    root = parse(text)
    exempt = suppressed_lines(root, _ALLOW)
    hits: list[int] = []
    for node in walk(root):
        words = command_words(node)
        if not words or not _unretried_download(words):
            continue
        line = node.start_point[0] + 1
        if line not in exempt:
            hits.append(line)
    return sorted(hits)


def main(argv: list[str]) -> None:
    sys.exit(
        run_line_checks(
            argv,
            violations,
            "single-shot `curl … -o` download with no retry — a transient blip "
            "fails the install. Add `--retry 3 --retry-delay 2` (or wrap in "
            "`retry`/`retry_stdout` from lib-ci-retry.sh), or annotate "
            "`# curl-retry-ok: <reason>`.",
        )
    )


if __name__ == "__main__":
    main(sys.argv[1:])
