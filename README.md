# Claude Automation Template

A GitHub template that makes [Claude Code](https://docs.anthropic.com/en/docs/claude-code) work reliably on your repositories. It wires up git hooks, CI workflows, and Claude session hooks so that Claude can autonomously fix code, create PRs, and respond to `@claude` mentions—with safeguards to prevent broken code from shipping.

## Why Use This

**Without this template**, using Claude Code on a repo requires manually configuring hooks, writing CI workflows, and building guardrails against common failure modes (infinite retry loops, pushing broken code, inconsistent formatting).

**With this template**, you get all of that out of the box:

- **A solid starting CLAUDE.md**—upholds high code quality standards, including a self-critique loop that catches bugs before they leave the editor
- **Pre-push verification**—build, lint, type checks, and tests run automatically before every `git push` or `gh pr create`
- **Deadlock-proof session hooks**—every hook is syntax-checked at session start, wrapped in a launcher that degrades to “ask” on parse failure, and commits with conflict markers are rejected up front
- **Skill-driven PR flow**—the `pr-creation` skill runs an iterative compress-critique-fix loop on the diff, then watches CI and fixes failures before reporting back
- **Enforced code quality**—Conventional Commits (via commitlint), Prettier formatting, and lint-staged run on every commit
- **`@claude` GitHub integration**—mention Claude in issues or PR comments and it responds with full repo context
- **Weekly security sweeps**—a scheduled workflow collects Dependabot, code-scanning, secret-scanning, and `pnpm audit` alerts, then hands them to Claude to open a rollup fix PR
- **Automatic template sync**—downstream repos receive improvements daily via PR, with 3-way merge that preserves your customizations
- **Multi-language support**—Node.js (pnpm), Python (uv/ruff/pytest), and shell (shfmt/shellcheck) work out of the box

## Prerequisites

- [Node.js](https://nodejs.org/) (see `.nvmrc` for the pinned version)
- [pnpm](https://pnpm.io/) (`npm install -g pnpm` if you don’t have it—`setup.sh` handles this automatically)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- (Optional) [uv](https://docs.astral.sh/uv/) for Python projects

## Quick Start

1. **Create your repo**—click **“Use this template”** on GitHub.
2. **Clone and set up:**

   ```bash
   git clone <your-repo-url>
   cd <your-repo>
   ./setup.sh
   ```

   This installs dependencies and configures git hooks. Verify the output ends with `✓ Setup complete!`.

3. **Install the [Claude GitHub App](https://github.com/apps/claude)** to enable `@claude` mentions in issues and PRs.

4. **Customize for your project:**
   - Edit **`CLAUDE.md`**—add project-specific context, architecture notes, and conventions for Claude.
   - Edit **`package.json`**—wire up your `dev`, `build`, `test`, `lint`, and `check` scripts. Unconfigured scripts are detected and skipped gracefully, so nothing breaks on first push.

## What’s Included

### Git Hooks (`.hooks/`)

| Hook          | What it does                                                                                 |
| ------------- | -------------------------------------------------------------------------------------------- |
| `pre-commit`  | Runs lint-staged—auto-formats with Prettier, shfmt, and ruff depending on file type          |
| `commit-msg`  | Validates [Conventional Commits](https://www.conventionalcommits.org/) format via commitlint |
| `lint-skills` | Lint-staged helper—validates skill files have required frontmatter (`name`, `description`)   |

### Claude Session Hooks (`.claude/hooks/`)

These run inside Claude Code sessions (local CLI or cloud), not in CI.

| Hook           | What it does                                                              |
| -------------- | ------------------------------------------------------------------------- |
| `SessionStart` | Installs tools (shfmt, shellcheck), configures git, installs dependencies |
| `PreToolUse`   | Runs build/lint/typecheck/tests before `git push` or `gh pr create`       |

### Claude Skills (`.claude/skills/`)

| Skill                  | What it does                                                                    |
| ---------------------- | ------------------------------------------------------------------------------- |
| `pr-creation`          | Self-critique workflow before PR submission, then watches CI and fixes failures |
| `update-pr`            | Updates an existing PR with new changes and optionally revises the description  |
| `conventional-commits` | Guides Claude through properly formatted commits with secret detection          |
| `markdown-block`       | Outputs content in a fenced code block so users can copy raw markdown           |
| `peer-review`          | Runs the read-only `code-reviewer` agent on the diff, then triages and fixes    |
| `explore-plan`         | Enforces the Explore → Plan → Review → Verify discipline for non-trivial work   |

### Claude Subagents (`.claude/agents/`)

| Agent           | What it does                                                                         |
| --------------- | ------------------------------------------------------------------------------------ |
| `code-reviewer` | Read-only reviewer (Read/Grep/Glob, `model: opus`)—unbiased second opinion on a diff |

### GitHub Actions (`.github/workflows/`)

| Workflow                           | What it does                                                          |
| ---------------------------------- | --------------------------------------------------------------------- |
| `claude.yaml`                      | Responds to `@claude` mentions in issues and PR comments              |
| `template-sync.yaml`               | Daily sync from template repo with 3-way merge and conflict detection |
| `phone-home.yaml`                  | Propagates “Lessons Learned” from merged PRs back to the template     |
| `security-vulnerability-scan.yaml` | Weekly security sweep—collects alerts, opens a rollup fix PR          |
| `node-tests.yaml`                  | Runs `pnpm test` (skips gracefully if unconfigured)                   |
| `lint.yaml`                        | Runs `pnpm lint` and `pnpm check` (skips gracefully if unconfigured)  |
| `format-check.yaml`                | Checks Prettier formatting                                            |
| `pre-commit.yaml`                  | Runs pre-commit hooks in CI                                           |
| `validate-config.yaml`             | Validates `.claude/` and `.hooks/` config on every push               |
| `dependabot-auto-merge.yaml`       | Auto-merges minor/patch Dependabot PRs after CI passes                |
| `auto-version.yaml`                | Post-merge, publishes to npm and tags `vX.Y.Z` (non-private packages) |

#### Required checks & branch protection

Each PR-gating workflow (`format-check`, `lint`, `node-tests`, `pre-commit`, `validate-config`) ends with an `if: always()` summary job—`format-check-passed`, `lint-passed`, `node-tests-passed`, `pre-commit-passed`, `validate-config-passed`—that `needs:` the real job(s) and passes only when they all succeed (or skip). **Mark these `*-passed` jobs as Required in branch protection, not the underlying jobs.** A job that is cancelled or skipped never reports a status to GitHub, so a directly-Required job can leave a PR stuck “pending” forever; the always-running summary job (`if: always()` plus a `contains(needs.*.result, …)` guard) reports a definitive pass/fail instead.

> **Caveat:** the summary job only helps when its workflow runs at all. `lint`, `node-tests`, and `validate-config` use `paths` filters, so on a PR that doesn’t touch their paths the _entire_ workflow (summary job included) is skipped and posts nothing. If you mark those `*-passed` checks Required, drop the workflow’s `paths` filter (let the job run and short-circuit internally) so the gate always reports.

### Releases & changelog (npm packages only)

`auto-version.yaml` automates npm releases for repos published as a **versioned npm package**. On every push to the default branch, [`.github/scripts/version-bump.sh`](.github/scripts/version-bump.sh):

1. Reads the latest published version from npm (the registry is the source of truth—the version is **never committed** to `package.json`).
2. Decides a [Conventional Commits](https://www.conventionalcommits.org/) semver bump from the commits since the last `vX.Y.Z` tag (`feat!`/`BREAKING CHANGE` → major, `feat` → minor, else patch).
3. Publishes to npm with `pnpm publish --provenance` via **OIDC trusted publishing** (`id-token: write`, so no `NPM_TOKEN`), then promotes the `## Unreleased` block in `CHANGELOG.md` into a dated section (drafting the prose with Claude when that block is empty) and pushes the doc commit plus the new tag.

> **Self-publish guard:** `version-bump.sh` exits early when `package.json` has `"private": true` (the template's own default), so the template never publishes itself. A consumer **opts in** by dropping `private` and setting a real, publishable `name`.
>
> **Not an npm package?** A repo that isn't published to npm (e.g. a website) should opt out by adding the release-flow files to `EXCLUDE_PATHS` in `template-sync.yaml`—the full list is documented in that file. `CHANGELOG.md` lives outside the synced paths, so a versioned consumer must create it to bootstrap the flow.

### MCP Servers (`.mcp.json`)

Team-shared [MCP servers](https://modelcontextprotocol.io/) live in `.mcp.json` at the repo root. A starter `.mcp.json.example` is included with GitHub, Context7, and Playwright entries:

```bash
cp .mcp.json.example .mcp.json   # then edit, set any referenced env vars, and run /mcp to verify
```

**Resist tool bloat**—each server expands Claude’s reasoning overhead, so enable only the ones you actually use and add more on demand. Personal (non-shared) servers belong in `~/.claude.json`, not the committed `.mcp.json`.

### Session Tuning (`.claude/settings.json` env)

The `env` block in `.claude/settings.json` sets defaults tuned for long-running web/automation sessions:

| Variable                                     | Why                                                                    |
| -------------------------------------------- | ---------------------------------------------------------------------- |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW=400000`     | Compacts earlier to curb context rot on long sessions (tune to taste)  |
| `CLAUDE_CODE_AUTO_BACKGROUND_TASKS=1`        | Auto-backgrounds long-running commands instead of blocking the session |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | Disables autoupdater/telemetry/error reporting (CI- and web-safe)      |

See the [Claude Code environment variables reference](https://code.claude.com/docs/en/env-vars) for the full list.

## How the Pieces Fit Together

```
Developer / Claude Code session
        │
        ├── git commit
        │     ├── pre-commit hook  → lint-staged (Prettier, shfmt, ruff)
        │     └── commit-msg hook  → commitlint (Conventional Commits)
        │
        ├── git push / gh pr create
        │     └── PreToolUse hook  → build + lint + typecheck + tests
        │
        └── /pr-creation skill    → self-critique loop → create PR → watch CI
                                                                │
GitHub Actions (CI)                                             ▼
        ├── format-check.yaml     → Prettier
        ├── lint.yaml             → pnpm lint + pnpm check
        ├── node-tests.yaml       → pnpm test
        ├── pre-commit.yaml       → pre-commit hooks
        ├── validate-config.yaml  → .claude/ and .hooks/ validation
        │
        ├── claude.yaml           → @claude mentions in issues/PRs
        ├── template-sync.yaml    → daily template updates (9am UTC)
        ├── phone-home.yaml       → sends Lessons Learned back to template
        ├── security-*.yaml       → weekly vulnerability sweep + fix PR
        └── dependabot-*.yaml     → auto-merge minor/patch dependency bumps
```

## Automatic Updates

Template improvements sync daily at 9am UTC via `template-sync.yaml`. You can also trigger manually from **Actions > Sync from Template**.

Changes arrive as a PR for you to review. The sync uses a 3-way merge that preserves local customizations in synced files—if there’s a conflict, Claude is asked to resolve it while keeping your project-specific changes intact.

### Secrets & repository settings

Repository **settings and secrets are never copied** when you create a repo from a template or when `template-sync` runs—both only move files. So each consuming repo configures these once. The workflows read:

| Secret                | Used by                                                 | Required?                             |
| --------------------- | ------------------------------------------------------- | ------------------------------------- |
| `ANTHROPIC_API_KEY`   | `claude`, `security-vulnerability-scan`, `auto-version` | For Claude-backed workflows           |
| `TEMPLATE_SYNC_TOKEN` | `template-sync`, `phone-home`, `auto-version`           | Optional—falls back to `GITHUB_TOKEN` |
| `PUSH_TOKEN`          | `security-vulnerability-scan`                           | Optional—falls back to `GITHUB_TOKEN` |

`TEMPLATE_SYNC_TOKEN` should be a **fine-grained PAT** (it lets sync/release PRs touch workflow files and clear tag protection, which `GITHUB_TOKEN` can’t):

| Permission      | Access         |
| --------------- | -------------- |
| `contents`      | Read and write |
| `workflows`     | Read and write |
| `pull requests` | Read and write |

**Enable [GitHub security features](https://docs.github.com/en/code-security) per repo** (Settings → Code security): secret scanning, push protection, and Dependabot alerts + security updates. The committed `.github/dependabot.yml` assumes Dependabot is on at the repo level. These are settings, not files, so they don’t sync—turn them on when you adopt the template.

> **Doing this across many repos?** Hosting them in a GitHub **organization** lets you set the secrets above **once as org secrets** (scoped to all repos) and enable the security features above via org-level **default code-security settings**, so every new repo inherits them with zero per-repo work. The org route is an optional convenience—the template works identically on a personal account, you just configure each repo individually.

## Project Structure

```
.
├── .claude/
│   ├── hooks/              # Claude session hooks (SessionStart, PreToolUse)
│   ├── skills/             # Claude skills (pr-creation, peer-review, explore-plan, ...)
│   ├── agents/             # Claude subagents (code-reviewer)
│   └── settings.json       # Claude Code hooks + session env tuning
├── .mcp.json.example       # Starter team-shared MCP servers (copy to .mcp.json)
├── .hooks/                 # Git hooks (pre-commit, commit-msg, lint-skills)
├── .github/
│   ├── workflows/          # CI workflows
│   └── dependabot.yml      # Dependabot configuration
├── config/                 # Shared configuration (e.g., JavaScript linting)
├── tests/                  # Python tests for hooks and config validation
├── CHANGELOG.md            # Changelog; auto-version promotes "## Unreleased" on release (npm packages)
├── CLAUDE.md               # Instructions for Claude Code sessions
├── package.json            # Node.js deps + lint-staged config
├── pyproject.toml          # Python project config (ruff, pytest)
└── setup.sh                # One-command setup script
```
