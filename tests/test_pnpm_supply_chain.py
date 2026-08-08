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
    return yaml.safe_load(WORKSPACE.read_text())


def _github_owner(repository_url: str) -> str:
    """Owner segment of a github.com repository URL, lowercased.

    npm `repository.url` values vary in prefix (`git+https://`, `git://`,
    `ssh://git@`) but all carry `github.com/<owner>/<repo>`, so the owner is the
    segment after the host rather than a fixed index.
    """
    _, _, after_host = repository_url.partition("github.com")
    owner = after_host.strip(":/").split("/")[0]
    assert owner, f"no github.com owner in repository url {repository_url!r}"
    return owner.lower()


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
        text = path.read_text().lower()
        # Comments legitimately name the inert spelling to warn about it; only a
        # line that actually sets it is a problem.
        settings = [
            line for line in text.splitlines() if not line.strip().startswith("#")
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
    manifest = json.loads(PACKAGE_JSON.read_text())
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
    owner = _github_owner(json.loads(PACKAGE_JSON.read_text())["repository"]["url"])

    for name in exempted:
        installed = Path(NODE_MODULES, name, "package.json")
        assert installed.is_file(), (
            f"{name} is exempt from the release-age floor but is not installed at "
            f"{installed}; run `pnpm install` and re-run."
        )
        repository = json.loads(installed.read_text()).get("repository")
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
