"""Every workflow that reaches the template repo must name the same owner.

`TEMPLATE_REPO` is declared once per workflow that crosses the repo boundary —
template-sync clones the template, phone-home files an issue on it. Each
declaration carries its own fallback owner, and a fallback naming an account
that does not host the template fails in the quietest way available: the sync
clones nothing new, or phone-home posts a downstream repo's lessons into the
void. No error, no red check, just a workflow that has stopped doing its job.

Two downstream repos were already patched by hand to the right owner, which is
what a defect that cannot announce itself looks like from the outside.

This iterates the declarations rather than naming them, so a workflow added
later is covered without an edit here.

A single source for the fallback is not available: GitHub Actions shares no
environment across workflow files, so each one that crosses the repo boundary
must carry the literal itself. `vars.TEMPLATE_SYNC_ORG` is the real override a
fork sets once; the literal is only what a repo gets when nobody sets it.
"""

import re

import pytest

from tests._helpers import REPO_ROOT

WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# The account that actually hosts this template. The `vars.TEMPLATE_SYNC_ORG`
# override still redirects a fork; this is only what a repo gets when nobody
# sets that variable.
EXPECTED_OWNER = "AlexanderMattTurner"

DECLARATION = re.compile(
    r"TEMPLATE_REPO:\s*\"\$\{\{\s*vars\.TEMPLATE_SYNC_ORG\s*\|\|\s*"
    r"'(?P<owner>[^']+)'\s*\}\}/(?P<repo>[^\"]+)\""
)


def _declarations() -> list[tuple[str, str, str]]:
    """(workflow name, owner, repo) for every TEMPLATE_REPO declaration."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yaml")):
        for match in DECLARATION.finditer(path.read_text(encoding="utf-8")):
            found.append((path.name, match["owner"], match["repo"]))
    return found


def test_the_declarations_are_still_here():
    # Without this the parametrized test below silently covers nothing: a
    # renamed variable or a changed quoting style would empty the list and every
    # case would pass by never running.
    names = {name for name, _, _ in _declarations()}
    assert {"template-sync.yaml", "phone-home.yaml"} <= names, (
        f"TEMPLATE_REPO declarations found in {names or 'no workflow'}"
    )


@pytest.mark.parametrize("workflow,owner,repo", _declarations())
def test_every_declaration_names_the_hosting_owner(workflow, owner, repo):
    assert owner == EXPECTED_OWNER, (
        f"{workflow} falls back to '{owner}', which does not host this template"
    )
    assert repo == "claude-automation-template"
