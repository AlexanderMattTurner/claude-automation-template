# Portability & Usability Analysis

An honest assessment of where this template creates friction compared to vanilla
Claude Code, and how it could better support integration with existing setups.

## Where This Is NOT Strictly Better Than Vanilla Claude Code

### 1. Node.js dependency tax on non-Node projects

The entire hook system depends on Node.js: `lint-staged`, `commitlint`, and
`prettier` all live in `node_modules`. A pure Python, Go, or Rust project gets
saddled with `package.json`, `pnpm-lock.yaml`, and a `node_modules` tree just to
get commit hooks working. Vanilla Claude Code imposes no such dependency.

**Files involved:** `package.json`, `.hooks/pre-commit`, `.hooks/commit-msg`

### 2. Hard opinion on pnpm

Every script, hook, and workflow assumes pnpm. Teams using npm, yarn, or bun must
either migrate or rewrite every reference. The `postinstall` script in
`package.json` silently runs `git config core.hooksPath .hooks`, which also
overrides any existing hook manager (husky, lefthook, simple-git-hooks).

**Files involved:** `package.json`, `setup.sh`, `.hooks/*`,
`.github/actions/setup-base-env/action.yaml`

### 3. Conventional Commits enforced, not optional

The `commit-msg` hook rejects non-conventional messages. Many teams use
squash-merge workflows where individual commit messages don’t matter, or have
their own commit conventions. There is no way to opt out without removing the
hook.

**Files involved:** `.hooks/commit-msg`,
`config/javascript/commitlint.config.js`

### 4. Prettier as the only formatter

Hardcoded in `lint-staged`. Teams using Biome, dprint, or project-specific
formatting configs collide. The `format-check.yaml` workflow also runs Prettier
specifically.

**Files involved:** `package.json` (`lint-staged` section), `.prettierrc.json`,
`.github/workflows/format-check.yaml`

### 5. Pre-push gate runs the entire test suite

The `pre-push-check.sh` hook (triggered via the `PreToolUse` Claude hook on
`git push`) runs build, lint, typecheck, AND tests. On large projects this can
take minutes per push. Most teams run this in CI, not as a local blocking gate.
Vanilla Claude Code lets you push immediately and iterate on CI feedback.

**Files involved:** `.claude/hooks/pre-push-check.sh`,
`.claude/settings.json` (PreToolUse matcher)

### 6. Stub scripts that look like failures

All five core scripts (`dev`, `build`, `test`, `lint`, `check`) emit
`ERROR: Configure X script in package.json` and `exit 1`. The pre-push hook
silently skips them (via `has_script` checking for the “ERROR” string), but
anyone running `pnpm test` directly sees what looks like a broken install.

**Files involved:** `package.json` (scripts section),
`.claude/hooks/lib-checks.sh` (`has_script` function)

### 7. SessionStart installs tools globally without asking

`session-setup.sh` installs shfmt, gh, jq, and shellcheck via webi and apt. This
may conflict with versions managed by nix, brew, asdf, or mise. In web sessions
this is fine; on a local machine it’s presumptuous.

**Files involved:** `.claude/hooks/session-setup.sh`

### 8. Template sync is a maintenance tax

The daily `template-sync.yaml` creates PRs that need review. The 3-way merge
handles some customizations, but conflict resolution falls to Claude via `@claude`
mentions, which requires an `ANTHROPIC_API_KEY` secret. If you don’t want the
template’s cadence of changes, you’re reviewing and closing noise PRs.

**Files involved:** `.github/workflows/template-sync.yaml`,
`.github/scripts/template-sync.sh`

### 9. Phone-home workflow leaks repo activity

`phone-home.yaml` sends “Lessons Learned” sections from your merged PRs to the
template repo as issues. This is opt-in (requires the `phone-home` label), but
the workflow itself is present and runs on every PR merge event.
Privacy-conscious organizations may not want this plumbing in their repos at all.

**Files involved:** `.github/workflows/phone-home.yaml`

### 10. Python tooling assumptions

The template commits `pyproject.toml` and `uv.lock` even if you’re not using
Python. For Python projects, it assumes ruff and uv—teams using black, flake8,
mypy, pip, or poetry get no support and must reconfigure.

**Files involved:** `pyproject.toml`, `uv.lock`,
`.pre-commit-config.yaml` (ruff hooks)

### 11. Large, opinionated CLAUDE.md

The CLAUDE.md is extensive (smart quotes policy, shell script error handling
patterns, CI pitfalls, testing patterns). These are good defaults but they
override Claude’s behavior globally. If you have your own CLAUDE.md, merging is
nontrivial and the template-sync will keep trying to overwrite yours.

**Files involved:** `CLAUDE.md`

### 12. GitHub-only CI

All workflows are GitHub Actions. GitLab CI, Bitbucket Pipelines, CircleCI, or
self-hosted setups get zero value from roughly half the template’s files.

**Files involved:** `.github/workflows/*`

---

## Integration Barriers With Existing Setups

### Merging existing `.claude/` configuration

If you already have `settings.json` with hooks, the template’s version overwrites
yours. There is no merge strategy—it’s a file replacement. Your existing
`SessionStart`, `PreToolUse`, or `PostToolUse` hooks are gone unless you manually
re-add them after sync.

### Existing skills collide

The template syncs the entire `.claude` directory. If you’ve written your own
skills with the same names (`pr-creation`, `conventional-commits`), the template
overwrites them. If you’ve written skills with different names, they’re preserved
but the template’s CLAUDE.md may not reference them.

### No monorepo support

The template assumes a single project root with one `package.json`, one
`pyproject.toml`. Monorepos with multiple packages, languages, or build systems
aren’t addressed. The pre-push hook runs checks against the root—it won’t
navigate to the right subdirectory.

### Workflow duplication

The template ships `lint.yaml`, `format-check.yaml`, `node-tests.yaml`, and
`pre-commit.yaml`. If your repo already has CI, you get duplicate workflows. The
`EXCLUDE_PATHS` env var in `template-sync.yaml` lets you skip specific files, but
you have to discover this yourself and maintain the exclusion list.

### Git hook manager conflict

The `postinstall` script sets `core.hooksPath` to `.hooks/`, which silently
disables any hooks managed by husky, lefthook, or simple-git-hooks. These tools
typically use `.husky/` or their own directory, and the override means they stop
running without any warning.

---

## Recommendations

### 1. Make the Node dependency optional

Use a language-detection approach: if `package.json` exists, use
lint-staged/commitlint. If not, use pre-commit (Python) or a simple shell-based
hook that calls whatever linter the project already has. The `.pre-commit-config.yaml`
already exists but isn’t wired into the local hooks path.

### 2. Support a `.template-config.yaml` for user choices

Let users declare their preferences: package manager, formatter, commit
convention, which workflows to include. Template-sync reads this file and respects
it, rather than treating every file as template-owned.

### 3. Ship CLAUDE.md instructions as composable fragments

Rather than one monolithic file, provide sections (CI guidance, testing patterns,
code style) that a user can opt into. Or provide a “minimal” vs “full” mode. This
makes it possible to adopt the Claude configuration without inheriting every
opinion.

### 4. Make template-sync opt-in, not opt-out

Remove the daily cron by default; let users enable it when they want it. Or
provide a one-shot “import latest” command instead of a persistent workflow. Most
users can manually pull improvements when they need them.

### 5. Separate Claude configuration from project tooling

The `.claude/` directory (hooks, skills, settings) is genuinely useful and mostly
non-conflicting. The git hooks, CI workflows, and formatting config are where
conflicts arise. These should be independently adoptable—perhaps as separate
template layers or opt-in modules.

### 6. Remove phone-home by default

Make it something users explicitly add if they want to contribute back, rather
than something they have to notice and remove.

### 7. Document the “eject” path

If someone wants to stop using the template and keep what they like, there should
be clear instructions: which files to keep, which to delete, what settings to
change, and how to disable template-sync permanently.

### 8. Detect existing hook managers

Before overriding `core.hooksPath`, check if husky, lefthook, or
simple-git-hooks are configured. If so, integrate with them rather than replacing
them, or at minimum warn the user.

---

## Summary

The template is most valuable for **greenfield projects** or repos with **no
existing Claude Code configuration, no existing CI, and no strong tooling
opinions.** For those cases, it’s a genuine accelerator.

For **existing projects with established tooling**, the template is closer to a
migration project than a drop-in improvement. The tight coupling between Claude
Code configuration (useful) and project tooling opinions (often conflicting) means
you can’t easily adopt one without the other. The template-sync mechanism
amplifies this—it’s great if you’re fully bought in, but becomes friction if
you’ve customized heavily.

Vanilla Claude Code’s advantage is that it has **no opinions** and creates **no
maintenance burden**. This template’s advantage is that it has _good_ opinions and
automates the tedious parts—but only if those opinions align with yours.
