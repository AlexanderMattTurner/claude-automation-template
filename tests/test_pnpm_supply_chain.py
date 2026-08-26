"""pnpm's minimum-release-age supply-chain floor and its first-party exemptions.

Two ways this config dies silently, both covered here:

1. The floor goes inert. pnpm 11 honors `minimumReleaseAge` only in
   pnpm-workspace.yaml; the `.npmrc` spelling (`minimum-release-age`) parses
   without complaint and does nothing, so a plausible "tidy the config into
   .npmrc" edit disarms the guard while every check stays green.
2. The exemption list grows. Each entry is a package allowed to install the
   instant it publishes. That is only safe for packages we publish ourselves,
   so the list is held to that definition rather than to reviewer vigilance.
"""

import json
from pathlib import Path

import pytest
import yaml

from tests._helpers import REPO_ROOT

WORKSPACE = REPO_ROOT / "pnpm-workspace.yaml"
PACKAGE_JSON = REPO_ROOT / "package.json"
NPMRC = REPO_ROOT / ".npmrc"
NODE_MODULES = REPO_ROOT / "node_modules"

# Spellings pnpm does not honor. Only `minimumReleaseAge` in pnpm-workspace.yaml
# takes effect; these parse without error and leave the floor off.
INERT_SPELLINGS = ("minimum-release-age", "minimum_release_age")


def _workspace() -> dict:
    return yaml.safe_load(WORKSPACE.read_text(encoding="utf-8"))


def _github_owner(repository: str) -> str:
    """Owner of an npm `repository` value, lowercased.

    Accepts the full-URL forms, whose prefixes vary (`git+https://`, `git://`,
    `ssh://git@`) but which all carry `github.com/<owner>/<repo>`, and npm's
    `github:<owner>/<repo>` and bare `<owner>/<repo>` shorthands. Anything else
    — a non-GitHub host — has no owner to compare and fails loudly rather than
    granting an unverifiable exemption.
    """
    _, on_github, after_host = repository.partition("github.com")
    path = after_host if on_github else repository.removeprefix("github:")
    assert "://" not in path, (
        f"repository {repository!r} is not on github.com, so its owner cannot be "
        "compared against this repo's."
    )
    owner = path.strip(":/").split("/")[0]
    assert owner, f"no owner in repository value {repository!r}"
    return owner.lower()


@pytest.mark.parametrize(
    "repository",
    [
        "git+https://github.com/Owner/repo.git",
        "https://github.com/Owner/repo",
        "ssh://git@github.com/Owner/repo.git",
        "git://github.com/Owner/repo.git",
        "github:Owner/repo",
        "Owner/repo",
    ],
)
def test_github_owner_reads_every_npm_repository_form(repository: str) -> None:
    assert _github_owner(repository) == "owner"


def test_github_owner_rejects_a_non_github_host() -> None:
    """Fail closed: an owner we can't compare must never pass as first-party."""
    with pytest.raises(AssertionError, match="not on github.com"):
        _github_owner("git+https://gitlab.com/Owner/repo.git")


def test_minimum_release_age_is_set_where_pnpm_reads_it() -> None:
    age = _workspace().get("minimumReleaseAge")
    assert isinstance(age, int) and age > 0, (
        f"{WORKSPACE.name} must declare a positive `minimumReleaseAge` (minutes); "
        f"got {age!r}. Without it every dependency resolves to versions published "
        "seconds ago."
    )


def test_no_inert_minimum_release_age_spelling() -> None:
    """The .npmrc spelling is accepted and ignored — never let it be the only one."""
    for path in (NPMRC, WORKSPACE):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        # Comments legitimately name the inert spelling to warn about it; only a
        # line that actually sets it is a problem. .npmrc takes both ini comment
        # markers, so a commented-out setting is not an offender either.
        settings = [
            line
            for line in text.splitlines()
            if not line.strip().startswith(("#", ";"))
        ]
        for spelling in INERT_SPELLINGS:
            offenders = [line for line in settings if spelling in line]
            assert not offenders, (
                f"{path.name} sets `{spelling}`, which pnpm 11 silently ignores. "
                f"Use `minimumReleaseAge` in {WORKSPACE.name} instead: {offenders}"
            )


def _exempted() -> list[str]:
    return _workspace().get("minimumReleaseAgeExclude") or []


def test_exempted_packages_are_declared_dependencies() -> None:
    """A stale exemption is a landmine: if the name is ever re-added — or taken
    over by a typosquatter — it installs with no release-age floor at all."""
    manifest = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    declared = {
        **manifest.get("dependencies", {}),
        **manifest.get("devDependencies", {}),
        **manifest.get("optionalDependencies", {}),
    }
    undeclared = [name for name in _exempted() if name not in declared]
    assert not undeclared, (
        f"{WORKSPACE.name} exempts packages that {PACKAGE_JSON.name} does not "
        f"depend on: {undeclared}. Drop the stale entries."
    )


def test_exempted_packages_are_first_party() -> None:
    """Every exemption must be a package this repo's owner publishes.

    Read from the installed copy rather than the registry so the check needs no
    network; `pnpm install` runs before pytest in CI (setup-base-env).
    """
    exempted = _exempted()
    if not exempted:
        return

    assert NODE_MODULES.is_dir(), (
        f"cannot verify first-party ownership of {exempted}: {NODE_MODULES} is "
        "missing. Run `pnpm install` and re-run."
    )
    owner = _github_owner(
        json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["repository"]["url"]
    )

    for name in exempted:
        installed = Path(NODE_MODULES, name, "package.json")
        assert installed.is_file(), (
            f"{name} is exempt from the release-age floor but is not installed at "
            f"{installed}; run `pnpm install` and re-run."
        )
        repository = json.loads(installed.read_text(encoding="utf-8")).get("repository")
        url = repository.get("url") if isinstance(repository, dict) else repository
        assert url, (
            f"{name} is exempt from the release-age floor but publishes no "
            "repository URL, so its ownership cannot be verified."
        )
        assert _github_owner(url) == owner, (
            f"{name} is published from {_github_owner(url)!r}, not {owner!r}. Only "
            "first-party packages may skip the minimum release age — a third-party "
            "package here reopens the supply-chain window it exists to close."
        )
