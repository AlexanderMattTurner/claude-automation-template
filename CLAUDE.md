# CLAUDE.md

## Commands

```bash
pnpm install    # Install deps + configure git hooks
pnpm format     # Format with Prettier
pnpm dev / pnpm build / pnpm test / pnpm lint  # If configured in package.json
```

Use pnpm (not npm) for all package operations.

## Personal Notes

Keep recurring personal nitpicks and review-feedback patterns in `CLAUDE.local.md` (gitignored), separate from the committed project rules here. Prune entries as the habits become automatic, and promote anything that should apply team-wide into this file.

## Git Workflow

Commits MUST use [Conventional Commits](https://www.conventionalcommits.org/) (`<type>(<scope>): <desc>`). The `commit-msg` hook enforces this. Types: feat, fix, refactor, docs, test, chore, ci, style, perf, build. Use `!` for breaking changes.

## Pull Requests

Use the `/pr-creation` skill. Before writing a PR description, check for `CONTRIBUTING.md` or `.github/PULL_REQUEST_TEMPLATE.md` in the target repo and follow its conventions. **Never** include `claude.ai` URLs, session links, or AI-tool attribution links in PRs. Include a `## Lessons Learned` section **only** for generalizable changes to the template files (e.g., `.claude/`, `.hooks/`, `.github/workflows/`, `CLAUDE.md`, `setup.sh`) that would benefit other downstream repos—the `phone-home.yaml` workflow propagates these to the template repo on merge. Repo-specific fixes do not belong here. Each lesson must be actionable: specify **what** to change in the template, **where** (template file/component), and **why**. Delete the section entirely if there are no template-level lessons—empty or vague lessons create noise.

**Lessons only reach the template repo if they appear in the PR description**—lessons mentioned only in chat are never propagated by `phone-home.yaml` and are permanently lost.

## Code Style

- Fail loudly: throw errors over logging warnings for critical issues
- Let exceptions propagate—never use try/except unless there is a specific, necessary recovery action. Default to crashing on unexpected input
- Un-nest conditionals; combine related checks
- Smart quotes (U+201C/U+201D/U+2018/U+2019): use Unicode escapes in code, centralize constants, ask user to verify output
- Fail loudly with clear error messages, only remove error reporting if user asks specifically
- Shell scripts: never use `|| true` to silence an expected non-zero exit—it silently swallows unexpected failures too. Branch on the exit code instead: `cmd; rc=$?; [ "${rc:-0}" -le N ] || exit "$rc"`.

## Self-Critique Loop

Before declaring any non-trivial coding task done, **iteratively critique and fix your own work until you reach a fixed point.** Read what you actually wrote (not what you intended to write) as if it came from a developer you cannot stand—assume it is wrong until proven otherwise.

Each pass, hunt for: bugs, broken or missed edge cases, weakened/skipped/deleted tests, swallowed errors, dead code, unjustified abstractions, premature returns, broken invariants, sloppy naming, fragile assumptions, hidden coupling, scope creep beyond the request, comments that explain _what_ instead of *why*, anything that smells off. State each issue bluntly in one line, then fix it. Then re-review the fix—fixes introduce their own bugs.

Stop only when a full pass turns up **nothing** worth changing. Cap at ~5 passes; if you’re still finding real issues at pass 5, say so and ask the user rather than silently giving up. Skip the loop for trivial edits (typo fixes, single-line config tweaks, pure questions)—say so explicitly when you skip.

## CI / GitHub Actions

- **Extract significant inline scripts** from workflow YAML into standalone files under `.github/scripts/` so they can be linted, type-checked, and tested independently. Inline scripts in `run:` or `script:` blocks are invisible to linters, shellcheck, `@ts-check`, and test frameworks. Rule of thumb: if the inline block exceeds ~10 lines or contains branching logic, extract it. Shell scripts go in `.github/scripts/*.sh`; JS scripts used by `actions/github-script` go in `.github/scripts/*.js` (with `@ts-check` and JSDoc types) and are loaded via `require('./.github/scripts/foo.js')`. Keep trivial glue (single commands, simple output-setting) inline.
- **Pin all third-party GitHub Actions to commit SHAs** (with a `# vX.Y` comment). Mutable version tags let a compromised maintainer silently replace code. Example: `uses: actions/checkout@de0fac2...dd # v6`.
- Add the `ci:full-tests` label to PRs that modify Playwright tests or interaction behavior, so CI actually runs Playwright on the PR.
- **`paths` filter pitfall**: if a workflow uses `paths` on one trigger (e.g., `push`) but not the other (e.g., `pull_request`), the triggers fire on different sets of changes, leading to confusing behavior. Always keep `paths` filters consistent across both `push` and `pull_request` triggers.
- **Autofix workflow pitfalls**: When building a workflow that auto-fixes CI failures:
  - Trigger on `pull_request` directly, not `workflow_run`—with `workflow_run` the triggered job runs against the base branch (not the PR HEAD), log context must be fetched as an artifact, and the mismatch makes diagnosing failures error-prone.
  - Gate on a non-bot actor (e.g., `github.event.pull_request.user.type != 'Bot'`) from day one—bot-authored PRs (dependabot, etc.) are rejected by `claude-code-action`, so the workflow burns CI minutes and accomplishes nothing.
  - Don’t ship a static “recoverable” allowlist (lint/format/docstring)—it either duplicates pre-commit or requires human judgment about why a rule fires in this codebase. Let `claude-code-action` decide whether a failure has a tractable mechanical fix.
- Use `uv` (not `pip`) for Python tool installs in CI; use `uv python install <version>` instead of `actions/setup-python`’s tool-cache when pinning a specific Python version—this removes the runner-image dependency entirely.
- When `.pre-commit-config.yaml` pins `default_language_version`, the CI workflow must install that exact Python version explicitly—runner images drop versions on their own schedule. Keep the two in sync.

## Testing

- Never skip or weaken tests unless asked
- Parametrize for compactness; prefer exact equality assertions
- For interaction features/bugs: add Playwright e2e tests (mobile + desktop, verify visual state)

- Python tests: resolve the repo root via `git rev-parse --show-toplevel`, not `Path(__file__).resolve().parent.parent`—depth-based parent-walking silently breaks when test files are moved.
- Python tests: don’t add `from __future__ import annotations` unless you need runtime annotation introspection (`typing.get_type_hints()`, Pydantic, etc.)—`dict[str, str]`, `X | None`, etc. work natively in Python 3.9+.

### Hook Errors

**NEVER disable, bypass, or work around hooks.** If a hook fails, **tell the user** what failed and why, then fix the underlying issue. If any hook fails (SessionStart, PreToolUse, PostToolUse, Stop, or git hooks), you MUST:

1. **Warn prominently**—identify which hook, the error output, and files involved
2. **Propose a fix PR**—check `.claude/hooks/` or `.hooks/` for the source
3. **Assess scope**—repo-specific issues: fix here. General issues: also PR the [template repo](https://github.com/alexander-turner/claude-automation-template)
