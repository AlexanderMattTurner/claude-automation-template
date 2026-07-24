"""The centralized untrusted-data guard.

The guard against prompt injection used to be hand-written at each call site and
again in each prompt doc, in several different phrasings — so the weakest wording
was the real trust boundary wherever it happened to sit. It now lives once in
.github/prompts/untrusted-data-preamble.md and is prepended by the shared
claude-run action whenever a caller declares untrusted input files.

Centralizing traded one failure mode for another: a prompt doc no longer carries
its own guard, so the guard's presence now depends on the CALL SITE declaring
`untrusted_files`. These tests drive the real composer for the guard's content
and pin that coupling, so dropping the declaration can't silently disarm it.
"""

import subprocess

import yaml

from tests._helpers import REPO_ROOT

SCRIPT = REPO_ROOT / ".github" / "scripts" / "compose-claude-prompt.sh"
PREAMBLE = REPO_ROOT / ".github" / "prompts" / "untrusted-data-preamble.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PROMPTS = REPO_ROOT / ".github" / "prompts"

# Phrasings that mean "a guard was written here by hand". The canonical file is
# the only place any of them may appear.
GUARD_PHRASES = ("never as instructions", "never follow them", "analyze them, never")


def _compose(tmp_path, prompt="", untrusted="", preamble=None):
    """Run the real composer; return (returncode, composed_prompt_or_None)."""
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GITHUB_OUTPUT": str(out),
        "PROMPT": prompt,
        "UNTRUSTED_FILES": untrusted,
        "PREAMBLE": str(PREAMBLE if preamble is None else preamble),
    }
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return proc.returncode, None
    return proc.returncode, _parse_output(out.read_text(encoding="utf-8"))["prompt"]


def _parse_output(text):
    """Parse GITHUB_OUTPUT heredoc form into {key: value}."""
    result, lines, i = {}, text.split("\n"), 0
    while i < len(lines):
        if "<<" in lines[i]:
            key, delim = lines[i].split("<<", 1)
            body = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i])
                i += 1
            result[key] = "\n".join(body)
        i += 1
    return result


def test_guard_precedes_the_callers_prompt(tmp_path) -> None:
    """Ordering is the whole point: the agent must read the guard before it is
    told to go read the untrusted files."""
    rc, composed = _compose(tmp_path, prompt="REVIEW NOW", untrusted="diff: /d.txt")
    assert rc == 0
    assert composed.index("untrusted DATA") < composed.index("- diff: /d.txt")
    assert composed.index("- diff: /d.txt") < composed.index("REVIEW NOW")


def test_guard_text_is_the_canonical_file_verbatim(tmp_path) -> None:
    """The composer must not paraphrase — the canonical file IS the wording."""
    _, composed = _compose(tmp_path, prompt="x", untrusted="diff: /d.txt")
    assert PREAMBLE.read_text(encoding="utf-8").strip() in composed


def test_entries_are_normalized_to_one_bullet_each(tmp_path) -> None:
    """Blank lines dropped, indentation stripped, an already-bulleted entry not
    double-bulleted — so a caller's YAML block scalar renders predictably."""
    _, composed = _compose(
        tmp_path, prompt="x", untrusted="  a: /a\n\n- b: /b\n   \nc: /c\n"
    )
    listing = composed.split("Untrusted input files:\n", 1)[1].split("\n\nx")[0]
    assert listing.splitlines() == ["- a: /a", "- b: /b", "- c: /c"]


def test_no_declared_files_passes_the_prompt_through_verbatim(tmp_path) -> None:
    """A caller with no untrusted input gets no preamble — and an empty prompt
    stays empty, preserving the action's event-driven tag mode."""
    assert _compose(tmp_path, prompt="just this")[1] == "just this"
    assert _compose(tmp_path, prompt="")[1] == ""


def test_missing_guard_file_fails_closed(tmp_path) -> None:
    """A declared-untrusted run must never reach the model unguarded: if the
    canonical file is missing, refuse rather than compose without it."""
    rc, _ = _compose(
        tmp_path, prompt="x", untrusted="diff: /d", preamble=tmp_path / "nope.md"
    )
    assert rc == 1


def test_prompt_cannot_forge_extra_github_outputs(tmp_path) -> None:
    """The composed value crosses GITHUB_OUTPUT, a line-oriented channel. A
    prompt carrying heredoc syntax must not be able to close the block early and
    have its tail re-parsed as further outputs."""
    hostile = "prompt<<X\nmalicious=1\nX\nEOF\ninjected=2"
    out = tmp_path / "gh_output"
    out.write_text("", encoding="utf-8")
    subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GITHUB_OUTPUT": str(out),
            "PROMPT": hostile,
            "UNTRUSTED_FILES": "",
            "PREAMBLE": str(PREAMBLE),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = _parse_output(out.read_text(encoding="utf-8"))
    assert parsed["prompt"] == hostile
    assert "malicious" not in parsed and "injected" not in parsed


def _claude_run_sites():
    for path in sorted(WORKFLOWS.rglob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            continue
        for job in (doc.get("jobs") or {}).values():
            for step in (job or {}).get("steps") or []:
                if isinstance(step, dict) and step.get("uses", "").endswith(
                    "actions/claude-run"
                ):
                    yield f"{path.name}:{step.get('name')}", step


def test_known_untrusted_ingesting_call_sites_declare_their_files() -> None:
    """Coverage floor, not a derived property: these are the automations that
    feed repo/PR content to Claude. Their prompt docs no longer carry a guard of
    their own, so dropping `untrusted_files` here would leave the run genuinely
    unguarded. A new untrusted-ingesting automation belongs in this list."""
    required = {
        "Review the PR with Claude",
        "Review the merge deltas with Claude (Sonnet)",
        "Judge which threads (and any body hold) are addressed (Claude Haiku)",
        "Triage and fix with Claude",
    }
    declared = {
        step.get("name")
        for _, step in _claude_run_sites()
        if str((step.get("with") or {}).get("untrusted_files", "")).strip()
    }
    assert required <= declared, f"missing untrusted_files: {required - declared}"


def test_the_guard_is_not_re_worded_anywhere_else() -> None:
    """The canonical file must remain the ONLY place the guard is phrased. A
    prompt restating it re-introduces a second copy — several slightly different
    wordings, the weakest of which becomes a real trust boundary.

    Scoped to text that actually reaches the model — the `prompt:` inputs and the
    prompt docs — NOT workflow comments, which legitimately describe the design
    to human readers and reach no agent."""
    model_facing = {}
    for _, step in _claude_run_sites():
        prompt = str((step.get("with") or {}).get("prompt", ""))
        if prompt:
            model_facing[f"prompt at {step.get('name')}"] = prompt
    for path in PROMPTS.rglob("*.md"):
        if path != PREAMBLE:
            model_facing[str(path.relative_to(REPO_ROOT))] = path.read_text(
                encoding="utf-8"
            )

    offenders = [
        f"{where} ({phrase!r})"
        for where, text in model_facing.items()
        for phrase in GUARD_PHRASES
        if phrase in text.lower()
    ]
    # not-a-drift-guard: this asserts the OPPOSITE of a drift guard. It does not
    # compare two copies for agreement — the duplication was eliminated (one
    # canonical file, prepended by claude-run), and this asserts no second copy
    # is re-introduced. The collection is a list of offending sites, empty when
    # the SSOT is intact.
    assert offenders == [], f"guard re-worded at: {offenders}"
