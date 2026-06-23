# Org bootstrap

Provision and keep in sync the things a template **repo** can't carry on its own
— org-level **secrets**, branch-protection **rulesets**, and default **settings**
— across every template-based repo in a GitHub organization.

## Why this exists (and how it fits the other sync paths)

| Path                        | What it moves                                            | Direction                   |
| --------------------------- | -------------------------------------------------------- | --------------------------- |
| `template-sync.yaml`        | **Files** (workflows, hooks, configs)                    | template → downstream repos |
| `phone-home.yaml`           | **Lessons** from PR descriptions                         | downstream repos → template |
| `sync-required-checks.yaml` | A **repo** ruleset, from `# required-check:` annotations | within one repo             |
| **this** (`org-bootstrap/`) | **Secrets, an org ruleset, org/repo settings**           | org → all managed repos     |

Secrets and protection rules are account/org state, not files, so `template-sync`
can never carry them. This closes that gap.

> **Ruleset overlap, on purpose.** An org ruleset and the per-repo
> `sync-required-checks` workflow can both be active — GitHub evaluates every
> matching ruleset and a check is required if _any_ of them requires it, so they
> compose rather than fight. Two sane setups:
>
> - **Org-managed (simplest):** define required checks once here, in
>   `REQUIRED_CHECKS`, and skip the per-repo workflow + its `RULESET_SYNC_TOKEN`.
> - **Repo-managed (most precise):** keep the per-repo workflow as the source of
>   truth (it derives the set from each repo's own `# required-check:`
>   annotations, so repos that drop a check aren't over-gated) and use the org
>   ruleset only for repo-agnostic rules (block deletion, block force-push).
>
> Keep `REQUIRED_CHECKS` here in lockstep with the annotated reporter jobs in
> `.github/workflows/` either way.

## Prerequisites

- [`gh`](https://cli.github.com/) authenticated as an **org owner**, and `jq`.
- Token scopes: classic PAT with **`admin:org`** (org secrets, rulesets, org
  settings) **and `repo`** (per-repo merge settings). Fine-grained equivalent:
  organization **Administration: write** + **Secrets: write**, repository
  **Administration: write**. `gh auth login` or `GH_TOKEN=...` both work.

## Setup

```bash
cd org-bootstrap
cp config.example.sh config.sh      # config.sh is gitignored
$EDITOR config.sh                   # set ORG, MANAGED_TOPIC, checks, settings
```

Tag the repos you want managed with the `MANAGED_TOPIC` topic (default
`template-managed`) so unrelated org repos are never touched. Leave
`MANAGED_TOPIC` empty to manage every non-archived repo.

## Run

```bash
# Secret VALUES are read from the environment, never the config file:
export RULESET_SYNC_TOKEN=ghp_xxx

./bootstrap.sh secrets     # push org Actions secrets (skips names with no env value)
./bootstrap.sh ruleset     # create/update the org branch-protection ruleset
./bootstrap.sh defaults    # org defaults + per-repo merge settings
./bootstrap.sh all         # all three, in order
```

Every subcommand is **idempotent** — re-run after editing `config.sh` to
converge. The ruleset is matched by name (`RULESET_NAME`) and updated in place,
so re-runs never create duplicates. A secret name with no matching environment
variable is skipped with a warning, never blanked.

## What each subcommand does

- **`secrets`** — `gh secret set --org` for each name in `SECRET_NAMES`, at
  `SECRET_VISIBILITY`. This is how `RULESET_SYNC_TOKEN` (and any other shared
  secret) reaches all repos without per-repo copying.
- **`ruleset`** — one org ruleset targeting the default branch of all repos
  (`~ALL` / `~DEFAULT_BRANCH`): blocks deletion and force-push, requires the
  `REQUIRED_CHECKS` contexts, and optionally requires a PR.
- **`defaults`** — org base permission, repo-creation policy, and default
  `GITHUB_TOKEN` workflow permissions; then per-repo merge hygiene
  (squash-only, delete branch on merge, auto-merge) on each managed repo.

## Automating it

Run it manually after onboarding a repo, or wire it to a schedule. To run it
from Actions, store the admin PAT as an org secret and invoke `./bootstrap.sh
all` on a `schedule`/`workflow_dispatch` trigger — keep it **off** `pull_request`
so it never becomes a required check (same rule the `sync-required-checks`
workflow follows).
