# CLAUDE.md

## Working style

- No running commentary or filler—don’t narrate tool use, restate my request, or recap after each step. Just do the work.
- Save all explanation for the END: a short overview of what changed and how it fits, plus anything I need to run/use it. Proportional to the change.
- Be direct. Flag real risks once; skip caveats I didn’t ask for. Don’t claim it works unless you ran it or read the code.

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

Use the `/pr-creation` skill. For contributions to others’ repos, before writing a PR description, check for `CONTRIBUTING.md` or `.github/PULL_REQUEST_TEMPLATE.md` in the target repo and follow its conventions. **Never** include `claude.ai` URLs, session links, or AI-tool attribution links in PRs. Include a `## Lessons Learned` section **only** for generalizable changes to the template files (e.g., `.claude/`, `.hooks/`, `.github/workflows/`, `CLAUDE.md`, `setup.sh`) that would benefit other downstream repos—the `phone-home.yaml` workflow propagates these to the template repo on merge. Repo-specific fixes do not belong here. Each lesson must be actionable: specify **what** to change in the template, **where** (template file/component), and **why**. Delete the section entirely if there are no template-level lessons—empty or vague lessons create noise.

**Skip the `## Lessons Learned` section entirely when the PR targets the `claude-automation-template` repo itself.** `phone-home.yaml` propagates lessons _from_ downstream repos _into_ the template; a change made directly in the template is already there, so a lessons section here propagates nothing and is pure noise.

**Lessons only reach the template repo if they appear in the PR description**—lessons mentioned only in chat are never propagated by `phone-home.yaml` and are permanently lost.

## Code Style

- Fail loudly: throw errors over logging warnings for critical issues
- Let exceptions propagate—never use try/except unless there is a specific, necessary recovery action. Default to crashing on unexpected input
- Un-nest conditionals; combine related checks
- Smart quotes (U+201C/U+201D/U+2018/U+2019): use Unicode escapes in code, centralize constants, ask user to verify output
- Fail loudly with clear error messages, only remove error reporting if user asks specifically
- Shell scripts: never use `|| true` to silence an expected non-zero exit—it silently swallows unexpected failures too. Branch on the exit code instead: `cmd; rc=$?; [ "${rc:-0}" -le N ] || exit "$rc"`.
- **Iterating word-split command output under the shared `shellharden` + `shellcheck` hooks**: don’t write `for x in $(cmd)` — `shellharden` auto-quotes the `$(cmd)`, killing the split, and `shellcheck` then fails with `SC2066`. Don’t reach for `mapfile`/`readarray` either if the script must run on macOS bash 3.2 (it’s bash 4+). The form that satisfies both hooks and old bash is an array filled with a portable `while IFS= read -r line; do arr+=("$line"); done < <(cmd)` loop, consumed as `"${arr[@]}"`.
- **Keep a script’s `--help`/usage comment block contiguous above the first executable line.** A `--help` built by an awk/sed scan of the leading comment block stops at the first non-comment line, so flag docs placed after `set -euo pipefail` (or the first code line) are silently dropped. Have the test assert a real flag appears in the output, not just the program name.
- **Escape every metacharacter class in a single pass when embedding text into a shell/DSL.** Chained `.replace()` calls where a later pass can re-touch an earlier pass’s inserted escape character are the classic source of CodeQL’s _incomplete string escaping_ findings.

## Self-Critique Loop

Before declaring any non-trivial coding task done, **iteratively critique and fix your own work until you reach a fixed point.** Read what you actually wrote (not what you intended to write) as if it came from a developer you cannot stand—assume it is wrong until proven otherwise.

Each pass, hunt for: bugs, broken or missed edge cases, weakened/skipped/deleted tests, swallowed errors, dead code, unjustified abstractions, premature returns, broken invariants, sloppy naming, fragile assumptions, hidden coupling, scope creep beyond the request, comments that explain _what_ instead of *why*, anything that smells off. State each issue bluntly in one line, then fix it. Then re-review the fix—fixes introduce their own bugs.

Stop only when a full pass turns up **nothing** worth changing. Cap at ~5 passes; if you’re still finding real issues at pass 5, say so and ask the user rather than silently giving up. Skip the loop for trivial edits (typo fixes, single-line config tweaks, pure questions)—say so explicitly when you skip.

After completing any non-trivial task, briefly reflect on how you could have iterated faster. Consider: which investigations or tool calls could have run in parallel? Were there full sweeps you ran locally that CI would have caught anyway—could a targeted check (single file, single test, quick lint) have been faster? Could you have pushed earlier and delegated validation to CI? State each insight as one concrete line; skip this for trivial tasks.

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
- **Required checks: gate on an `if: always()` summary job, never the underlying job.** A job that is skipped (e.g. by a `paths` filter or `if:`) or cancelled posts no status, so a directly-Required job leaves PRs stuck “pending” forever. Add a summary job that `needs:` the real job(s), runs with `if: always()`, and fails via `if: contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')` + `run: exit 1`—then mark that summary job Required. Give each workflow’s summary job a distinct name (branch protection matches by name, so duplicates collide). Caveat: if the whole workflow is skipped by a `paths` filter, the summary job is skipped too—drop the `paths` filter on any workflow whose gate you mark Required.
- **A path-gated job must list every file it actually depends on.** When a shared module becomes an import dependency of jobs gated by a `paths:` filter, add it to _every_ such gate—not just some. A gate that omits a real dependency fails open: it skips the job exactly when that dependency changed.
- **Polling Docker Compose state in CI**: query `docker inspect` per container id (`docker compose ps -q <svc>`), not `docker compose ps --format json`. The latter emits a JSON array on some Compose versions and newline-delimited objects (JSONL) on others, so a fixed-shape parser silently yields no state and the readiness loop times out instead of failing fast.
- **Provision a guardrail hook’s runtime deps synchronously before backgrounding slow installs**, and smoke-test setup from a genuinely cold checkout. PostToolUse hooks fire on the first tool call, which can beat a backgrounded `uv sync`/`pnpm install`; a hook that fails closed on a missing dep (e.g. a secret-redactor missing `detect_secrets`) breaks silently during the cold-start window. Keep every hook-dependency installer above any `&`-backgrounded installs in `session-setup.sh`, and gate the ordering with a cold-checkout smoke test (`hook-lifecycle.yaml`) that installs only base-image prereqs.

## Testing

- Never skip or weaken tests unless asked
- Parametrize for compactness; prefer exact equality assertions
- For interaction features/bugs: add Playwright e2e tests (mobile + desktop, verify visual state)

- Python tests: resolve the repo root via `git rev-parse --show-toplevel`, not `Path(__file__).resolve().parent.parent`—depth-based parent-walking silently breaks when test files are moved.
- Python tests: don’t add `from __future__ import annotations` unless you need runtime annotation introspection (`typing.get_type_hints()`, Pydantic, etc.)—`dict[str, str]`, `X | None`, etc. work natively in Python 3.9+.
- **Don’t let guard tests pass vacuously.** A test that greps source for a pattern, or asserts a forbidden string is _absent_, keeps passing when the thing it checks silently stops happening—the matched idiom gets refactored to an equivalent (`source "$VAR/…"` → `source "${BASH_SOURCE[0]%/*}/…"`), or the code path that should emit the string no longer runs. Enumerate the accepted idioms and assert the match set is non-empty; pair every negative assertion with a positive marker proving you’re on the intended path.
- **SSOT contract tests must change in the same commit as their data.** When a deny/allow list, generated file, or doc has a round-trip test asserting “cases exactly cover the live config” or “committed output == regenerated output,” editing the source without updating the test is a silent CI break—the test _is_ the contract, so search for such a round-trip before landing any change to the data it guards. For marker-splice generators, the marker must be a comment in the target’s own syntax and must not appear in rendered output, so it can’t live inside a printed here-doc.
- **A surviving mutant can be a false equivalent.** If a guard’s only observable effect is gated behind a _throwing_ test fixture, it looks equivalent no matter what it does. Give the downstream path a non-throwing fixture (e.g. a readable file whose redactor throws a sentinel) so the guard’s decision becomes observable before accepting the mutant—the convergence is often a fixture artifact, not the code.
- **Mapping sanitized view text back to source bytes needs a uniqueness check.** A re-clean/round-trip self-check is necessary but not sufficient: a purely invisible character (zero-width, soft hyphen, BOM) vanishes on re-clean, so two distinct regions clean to the same text and a greedy aligner picks the wrong one. Also assert the anchor is unique in the source, not merely that it re-sanitizes correctly.

### Hook Errors

**NEVER disable, bypass, or work around hooks.** If a hook fails, **tell the user** what failed and why, then fix the underlying issue. If any hook fails (SessionStart, PreToolUse, PostToolUse, Stop, or git hooks), you MUST:

1. **Warn prominently**—identify which hook, the error output, and files involved
2. **Propose a fix PR**—check `.claude/hooks/` or `.hooks/` for the source
3. **Assess scope**—repo-specific issues: fix here. General issues: also PR the [template repo](https://github.com/alexander-turner/claude-automation-template)
