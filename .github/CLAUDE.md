> Loaded on demand when Claude reads a file under `.github/`, in addition to the root [`CLAUDE.md`](../CLAUDE.md). Workflow-**authoring** mechanics live here so they don't tax every unrelated session; the reactive doctrine — how to respond to a red check — lives in the `ci-triage` skill, which fires on the failure rather than on the path.

## Structure

- **Extract significant inline scripts** to `.github/scripts/`—inline `run:` blocks are invisible to shellcheck, `@ts-check`, and tests. Rule of thumb: >~10 lines or branching logic → extract. Keep trivial glue (single commands, simple output-setting) inline.
- **Pin all third-party GitHub Actions to commit SHAs** (with a `# vX.Y` comment). Mutable version tags let a compromised maintainer silently replace code. Example: `uses: actions/checkout@de0fac2...dd # v6`.
- Use `uv` (not `pip`) for Python tool installs in CI; use `uv python install <version>` instead of `actions/setup-python`'s tool-cache when pinning a specific Python version—this removes the runner-image dependency entirely.
- When `.pre-commit-config.yaml` pins `default_language_version`, the CI workflow must install that exact Python version explicitly—runner images drop versions on their own schedule. Keep the two in sync.

## Triggers, filters, and required checks

- **`paths` filter pitfall**: if a workflow uses `paths` on one trigger (e.g., `push`) but not the other (e.g., `pull_request`), the triggers fire on different sets of changes, leading to confusing behavior. Always keep `paths` filters consistent across both `push` and `pull_request` triggers.
- **Required checks: gate on an `if: always()` summary job, never the underlying job.** A skipped or cancelled job posts no status, leaving PRs stuck "pending" forever. Add a summary job (`needs:` the real jobs, `if: always()`, fails on failure/cancelled) and mark that Required instead. Give each summary job a distinct name (branch protection matches by name). Caveat: a whole-workflow `paths` filter also skips the summary—drop it on Required workflows.
- **A path-gated job must list every file it actually depends on.** When a shared module becomes an import dependency of jobs gated by a `paths:` filter, add it to _every_ such gate—not just some. A gate that omits a real dependency fails open: it skips the job exactly when that dependency changed. This also applies to test path filters: any test asserting a property of file X must have X in the filter that decides whether the test runs—a skipped test reports as passing, so a bot bump to X merges unverified.
- **A conditional CI gate can ship broken and stay green indefinitely.** A gate whose expensive job only runs on certain commits (via a `decide` job, `if:` condition, or `paths:` filter) may never fire after being introduced — the "green" you see is a skip, not a pass. When adding or changing a conditional gate, trigger it explicitly at least once on a commit that meets the condition, or add a lightweight always-run smoke-check of the gated script.
- **A check that cannot verify its subject must fail loud, not skip green.** When a check needs a capability to do its job (a device node, a running service, a credential), it must **require** that capability and go red with an actionable message when it is missing — never `echo "skipped"; exit 0`, never a `skipif` that turns can't-verify into a pass. A missing prerequisite is a red ("could NOT verify — provision a capable runner"), never a false green. (A scenario genuinely _inapplicable_ on a platform — a Linux-container e2e on macOS — is not-applicable, which is different from an under-provisioned runner.)

## Concurrency

- **Add a per-branch `concurrency` group to every PR-triggered workflow**: `group: "${{ github.workflow }}-${{ github.ref }}"`, `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`. A global group (not keyed by ref) cancels queued runs under contention, blocking required checks when a cancelled job posts no status. Exclude workflows with durable side effects on `pull_request: closed` (use `cancel-in-progress: false` there).

## Scoping work to a change range

- **CI jobs scoping work to a PR's change-range must derive the head from the checkout, not the event payload.** Resolve the range head with `git rev-parse HEAD` after `actions/checkout`, not `github.event.pull_request.head.sha`—the event SHA is frozen at trigger time; a rebase or force-push makes it point at a diverged commit, silently mis-scoping the range to the whole branch history.
- **A formatter wired only into the staged-files commit hook leaves a hole the merge button drives through.** GitHub squash/merge runs no client hooks; a 3-way merge can land formatting drift that a CI `format --check` then rejects on an otherwise-clean `main`. Wire the formatter into an all-files autofix step or CI job — not only the staged-files hook — so the check side and fix side cover the same surface.

## Tokens and the API

- **`GITHUB_TOKEN` cannot resolve review threads in Actions.** `resolveReviewThread` returns "Resource not accessible by integration" for the app installation token even with `pull-requests: write`; `addPullRequestReviewThreadReply` on the same thread succeeds. Any bot that auto-resolves conversations needs a PAT (a user-actor token) for the resolve mutation.
- **`gh api --paginate --jq` applies the jq filter per page.** A filter ending in a reducer (`last`, `first`, `max_by`, `add`) is silently wrong across a page boundary—it runs the reducer on each page separately. Add `--slurp` so all pages merge into one array and the reducer runs once over the full dataset.

## Autofix workflows

When building a workflow that auto-fixes CI failures:

- Trigger on `pull_request` directly, not `workflow_run`—with `workflow_run` the triggered job runs against the base branch (not the PR HEAD), log context must be fetched as an artifact, and the mismatch makes diagnosing failures error-prone.
- Gate on a non-bot actor (e.g., `github.event.pull_request.user.type != 'Bot'`) from day one—bot-authored PRs (dependabot, etc.) are rejected by `claude-code-action`, so the workflow burns CI minutes and accomplishes nothing.
- Don't ship a static "recoverable" allowlist (lint/format/docstring)—it either duplicates pre-commit or requires human judgment about why a rule fires in this codebase. Let `claude-code-action` decide whether a failure has a tractable mechanical fix.

## Lint ratchets

- **When a lint cap is disabled due to existing violations, replace it with a grandfathered ratchet, not silence.** Baseline current violators, cap new ones, and fail stale entries so the list only shrinks. The flat cap fails at adoption (existing violators block unrelated work)—but no cap is the worst outcome. The RuboCop-todo / pylint-todo shape works for any linter metric: file size, complexity, suppression counts.

## Every guard needs a watched surface

**Before calling a check done, ask "when this goes red, WHO SEES IT?"** A check is worth exactly what its failure surface is worth, and the surfaces are not equal: a required PR check blocks a merge and gets read, while a `schedule`, `workflow_dispatch`, or post-merge run has no PR surface at all — its red lands in an Actions tab nobody opens, so the check is decorative. Route the failure to a human (a notification action, an issue), or say why nobody needs to know.

**Then ask it about duration: "if this stayed broken for a month, what would tell me?"** A cron whose failures notify nobody is silent for exactly as long as it stays broken, and a month of that looks identical to a healthy quiet month. Its twin and the strictly worse case is the **vacuous green**: a check that fails to fetch its input, degrades to a placeholder, and reports success — no surface saves that, so pair the question with "what does this do when its input is missing?" and make the answer fail closed.
