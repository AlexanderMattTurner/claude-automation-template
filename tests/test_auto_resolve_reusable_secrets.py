"""The reusable resolver's `secrets:` declaration must cover what it reads.

PROBLEM CLASS — a secret a called workflow reads but does not DECLARE arrives
empty. It raises no error at any layer: the expression evaluates, the env var is
set to "", and the credential ladder reads the rung as dead and walks past it. A
consumer repository that configured the secret correctly then watches its paid
rungs silently do nothing, and the run reports "every credential billed nothing"
instead of naming the omission.

The declaration is also the contract a consumer configures against, so the
caller in this repository has to pass every declared name — a caller that
passes a subset is the same silent-empty failure with a different cause.
"""

import re

import yaml

from tests._helpers import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
REUSABLE = WORKFLOWS / "auto-resolve-reusable.yaml"
CALLER = WORKFLOWS / "auto-resolve-conflicts.yaml"

# Injected by Actions into every workflow, so it is never declared or passed.
AUTOMATIC = {"GITHUB_TOKEN"}

SECRET_REF = re.compile(r"\bsecrets\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


def _declared() -> set[str]:
    doc = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))
    # `on:` parses as the boolean True under YAML 1.1, which is what PyYAML
    # implements; read both spellings rather than betting on the loader.
    trigger = doc.get("on", doc.get(True))
    return set((trigger["workflow_call"].get("secrets") or {}).keys())


def _referenced() -> set[str]:
    """Every `secrets.NAME` the reusable workflow reads, minus the automatic one.

    Read from the text, not the parsed tree: a reference can sit anywhere an
    expression is allowed — a step `env:` value, a multi-line `>-` chain of
    conditionals, a `with:` input — and walking the tree for all of those is a
    second parser for a syntax the regex already covers exactly.
    """
    return set(SECRET_REF.findall(REUSABLE.read_text(encoding="utf-8"))) - AUTOMATIC


def test_every_secret_the_resolver_reads_is_declared() -> None:
    """RED when a step gains a `secrets.X` the `workflow_call` block omits — the
    case where X reaches the runner as an empty string and reads as a dead
    credential."""
    referenced = _referenced()
    assert referenced, (
        "read no secret references — every assertion here would pass over nothing"
    )
    assert referenced <= _declared(), f"undeclared: {sorted(referenced - _declared())}"


def test_no_declared_secret_is_unread() -> None:
    """The other direction: a declared name nothing reads is a contract entry a
    consumer configures for no effect."""
    declared = _declared()
    assert declared, (
        "read no declared secrets — the assertions below would pass over nothing"
    )
    assert declared <= _referenced(), (
        f"declared but unread: {sorted(declared - _referenced())}"
    )


def test_the_caller_passes_every_declared_secret() -> None:
    """A `secrets:` mapping that omits a name is indistinguishable at run time
    from a repository that never set it, so the omission has to fail here."""
    doc = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    secrets = doc["jobs"]["resolve"].get("secrets") or {}
    # `inherit` parses as a scalar string, not a mapping. Naming it here is what
    # keeps the wholesale hand-over a FAILURE rather than an AttributeError.
    assert isinstance(secrets, dict), (
        f"caller passes `secrets: {secrets}` instead of a named list"
    )
    assert set(secrets) == _declared(), (
        f"caller omits {sorted(_declared() - set(secrets))}; "
        f"passes undeclared {sorted(set(secrets) - _declared())}"
    )
