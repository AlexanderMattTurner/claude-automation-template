""".github/scripts/check-oauth-fallback.py — the job-scoped OAuth-fallback lint.

Drives the pure ``find_violations`` / ``_job_ranges`` over synthetic workflows and
asserts the job-scoping: a primary-token use whose enclosing job wires no
``CLAUDE_CODE_OAUTH_TOKEN_FALLBACK`` is flagged, even when a SIBLING job wires it
(the file-level blind spot this lint exists to close). The real workflow tree is
the compliant negative — every job using the primary token wires the fallback in
the same job.
"""

import importlib.util
import textwrap


from tests._helpers import REPO_ROOT

_SRC = REPO_ROOT / ".github" / "scripts" / "check-oauth-fallback.py"
_spec = importlib.util.spec_from_file_location("check_oauth_fallback", _SRC)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _wf(body: str) -> str:
    return textwrap.dedent(body).lstrip("\n")


def test_flags_job_with_primary_but_no_fallback() -> None:
    text = _wf(
        """
        name: x
        jobs:
          a:
            steps:
              - uses: ./.github/actions/claude-run
                with:
                  oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        """
    )
    assert mod.find_violations(text) != []


def test_accepts_primary_with_fallback_same_job() -> None:
    text = _wf(
        """
        name: x
        jobs:
          a:
            steps:
              - uses: ./.github/actions/claude-run
                with:
                  oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
                  fallback_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_FALLBACK }}
        """
    )
    assert mod.find_violations(text) == []


def test_sibling_job_fallback_does_not_clear_a_bare_job() -> None:
    # Job `a` wires the fallback; job `b` runs the primary token unprotected. A
    # file-level check would pass this; the job-scoped check must flag job `b`.
    text = _wf(
        """
        name: x
        jobs:
          a:
            steps:
              - with:
                  oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
                  fallback_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_FALLBACK }}
          b:
            steps:
              - with:
                  oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        """
    )
    hits = mod.find_violations(text)
    # Exactly one hit, on the line in job `b`.
    assert len(hits) == 1
    assert "fallback_oauth_token" not in text.splitlines()[hits[0] - 1]
    # The flagged line is below job `a`'s (protected) primary use.
    lines = text.splitlines()
    assert lines[hits[0] - 1].strip().startswith("oauth_token:")
    assert hits[0] > next(i for i, ln in enumerate(lines, 1) if "b:" == ln.strip())


def test_fallback_longer_identifier_is_not_a_primary_match() -> None:
    # The fallback line itself contains CLAUDE_CODE_OAUTH_TOKEN_FALLBACK — the `\b`
    # right boundary must NOT treat it as a bare primary-token use.
    text = _wf(
        """
        jobs:
          a:
            steps:
              - with:
                  fallback_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_FALLBACK }}
        """
    )
    assert mod.find_violations(text) == []


def test_same_line_allow_optout_clears() -> None:
    text = _wf(
        """
        jobs:
          a:
            steps:
              - run: use ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }} # allow-no-oauth-fallback: single-cred smoke test
        """
    )
    assert mod.find_violations(text) == []


def test_preceding_line_allow_optout_clears() -> None:
    text = _wf(
        """
        jobs:
          a:
            steps:
              # allow-no-oauth-fallback: single-cred smoke test
              - run: use ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        """
    )
    assert mod.find_violations(text) == []


def test_allow_without_reason_does_not_clear() -> None:
    text = _wf(
        """
        jobs:
          a:
            steps:
              - run: use ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }} # allow-no-oauth-fallback:
        """
    )
    assert mod.find_violations(text) != []


def test_top_level_env_uses_file_level_fallback() -> None:
    # A use outside every job (a workflow header comment / top-level env) degrades
    # to the file-level check: the fallback present anywhere in the file clears it.
    text = _wf(
        """
        # Uses CLAUDE_CODE_OAUTH_TOKEN with a fallback (see below).
        jobs:
          a:
            steps:
              - with:
                  oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
                  fallback_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_FALLBACK }}
        """
    )
    assert mod.find_violations(text) == []


def test_no_jobs_map_degrades_to_file_level() -> None:
    # A composite action (no top-level jobs:) has no job ranges; a bare primary
    # use with no fallback anywhere is still flagged via the file-level path.
    text = _wf(
        """
        inputs:
          oauth_token:
            description: uses secrets.CLAUDE_CODE_OAUTH_TOKEN
        runs:
          using: composite
        """
    )
    assert mod._job_ranges(text) == []
    assert mod.find_violations(text) != []


def test_real_workflows_are_compliant() -> None:
    wf_dir = REPO_ROOT / ".github" / "workflows"
    for path in sorted(wf_dir.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert mod.find_violations(text) == [], f"{path.name} has an unprotected use"
