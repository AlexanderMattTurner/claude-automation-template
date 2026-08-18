> Loaded on demand when Claude reads a file under `.github/`, in addition to the root [`CLAUDE.md`](../CLAUDE.md). Workflow-**authoring** mechanics live here so they don't tax every unrelated session; the reactive doctrine — how to respond to a red check — lives in the `ci-triage` skill, which fires on the failure rather than on the path. Most rules below are also enforced by a pre-commit hook, so this prose is reinforcement, not the sole guard.

## Structure

- **Every job that is (or could become) a required status check must have a `name:` that describes what it verifies**, not a restatement of the job ID — the name appears verbatim in the branch-protection UI and the PR check list. Bad: `pytest:`, `check:`. Good: `name: Python tests (pytest)`. For a matrix job, include the matrix variable: `name: Hook lifecycle (${{ matrix.os }})`.
- **Don't inline a substantial shell script in a `run:` block — extract it to `.github/scripts/<name>.sh` and call `run: bash .github/scripts/<name>.sh`.** Inline shell is invisible to the repo's shell linters (shellcheck and shfmt only see standalone `.sh` files), so a long inline block ships unchecked. Rule of thumb: more than ~10 lines, or any branching logic, gets extracted; trivial glue (one command, one output assignment) stays inline. **A job calling an external script must `actions/checkout` first** — a checkout-less decide or report job either adds `sparse-checkout: .github/scripts` or keeps the small block inline.
- **Pin every third-party action to a commit SHA** with a `# vX.Y` comment. A mutable tag lets a compromised maintainer replace the code you reviewed. Example: `uses: actions/checkout@9c091bb…9 # v7.0.0`.
- Use `uv` (not `pip`) for Python tool installs in CI, and `uv python install <version>` instead of `actions/setup-python`'s tool cache when pinning a version — that removes the runner-image dependency entirely.
- When `.pre-commit-config.yaml` pins `default_language_version`, the workflow must install that exact Python version. Runner images drop versions on their own schedule; keep the two in sync.

## Path filtering for required checks

**Never put a `paths:`/`paths-ignore:` — or `branches:`/`branches-ignore:` — filter on the `pull_request:` trigger of a required-check workflow.** When the filter does not match, the workflow never starts, so the check is never reported: GitHub shows "Expected — Waiting" forever and blocks the PR. Only `success` or `skipped` satisfies a required check; `cancelled` blocks it too. A `branches:` filter fails the same way through stacked PRs — a stack layer's base is another feature branch, so `branches: [main]` skips it and its required checks hang.

To skip expensive jobs on irrelevant PRs **without** hanging the check, gate at the **job** level:

1. Leave `pull_request:` with no `paths:`, so the workflow always fires and always reports.
2. Add a `decide` job — a `uses:` of [`decide-reusable.yaml`](workflows/decide-reusable.yaml) — with a `paths-regex` that diffs the change range and outputs `run`.
3. Give each real job `needs: decide` and `if: needs.decide.outputs.run == 'true'`, so when nothing relevant changed the job is skipped (a passing required check) and its runner never boots.
4. Add an `always()` reporter job (below) and register **its** name as the required check.

**The caller's `decide` job must grant every permission the reusable workflow declares** — `contents: read`, `pull-requests: read`, `actions: read`. GitHub lets a called workflow request only what the calling job already holds, and a caller that grants fewer does not go red: the whole run ends in `startup_failure`, so the reporter never posts and the required check hangs at "Expected — Waiting" forever. That is the exact failure the job-level gate exists to prevent, arriving through the gate itself.

The decide job diffs the pushed range (`before…sha`) on `push` and the batch range on `merge_group`, so post-merge runs are path-gated exactly as PR runs are. It returns `run=true` only where no diffable range exists (`workflow_dispatch`, `schedule`, a force-pushed or newly-created branch) — failing open, never silently skipping.

**A `paths-regex` is POSIX ERE, because `grep -E` is what reads it — never PCRE.** `(?:`, `\d`, `\w`, `\s` and `(?=` do not exist in ERE. GNU grep does not reject them: it warns on stderr and then matches with a DIFFERENT pattern, so `\.claude/(?:hooks/|settings\.json$)` silently becomes `\.claude/(:hooks/|settings\.json$)`, `.claude/hooks/` stops matching, and the required check greens without running. Write `(a|b)`, `[0-9]`, `[[:alnum:]_]`, `[[:space:]]`.

**A path-gated job must watch every file it actually depends on.** A gate that omits a real dependency fails open: the job skips exactly when that dependency changed, and the reporter greens the skip. This binds on tests too — a test asserting a property of file X must have X in the gate that decides whether it runs. Two inputs remove the hand-maintained copy where they apply: `pytest-targets` derives the watched paths from a Python test's own import lines, and `paths-regex-file` reads the regex from a shell snippet a local git hook sources.

**A decide-gated workflow takes no `paths:` filter on its `push:` trigger either.** The filter would be a hand-maintained twin of the `paths-regex` in GitHub's other path syntax, with nothing generating both from one source. The copy drifts in the direction that matters — narrower than the regex — and a merge to a regex-only path then skips the post-merge leg the PR leg ran. The decide job already skips that merge for a few seconds of a cheap runner, which is what the filter was buying.

**Only gate EXPENSIVE workflows.** For a job that takes a few seconds (actionlint, gitleaks), the decide job costs as much as the work, so let it always run.

## Required-check reporters and branch protection

**A required check gates on an `if: always()` reporter job, never on the underlying job.** A skipped or cancelled job posts no status and leaves the PR pending forever. The reporter `needs: [decide, <work-job>]`, runs `if: always()`, and delegates the 0/1 verdict to the shared [`report-job-result`](actions/report-job-result/action.yaml) composite (`run:` = `needs.decide.outputs.run`, `decide-result:` = `needs.decide.result`, `result:` = `needs.<work-job>.result`). Don't re-implement that bash; copy the shape from `hook-lifecycle.yaml`.

**Branch protection is synced from annotations, not edited by hand.** Every `always()` reporter on a PR-triggered workflow declares `# required-check: true` or `# required-check: false  # <reason>` on its key or a direct-child line. [`sync-required-checks.yaml`](workflows/sync-required-checks.yaml) reads those markers, expands each `name:` across the job's `strategy.matrix`, and rewrites the ruleset's required checks to exactly that set. So **to make a check required, mark its job `# required-check: true`; to drop one, flip it to `false` with a reason** — never edit the ruleset in the UI, or the next sync reverts you.

## Concurrency

**Add a per-branch `concurrency` group to every PR-triggered workflow**: `group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}`, `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`. Use `cancel-in-progress: false` for a workflow with durable side effects (anything on `pull_request: closed`).

**Never put a `concurrency:` lock with a _static_ (ref-less) `group:` at the workflow level on a required-check workflow.** A ref-less group serializes every ref through one slot, and GitHub cancels the older pending run wholesale when a newer one — even from another PR — arrives. A cancelled run starts **zero** jobs, so the reporter never executes and the required check hangs at "Expected — Waiting" forever. When you genuinely need global serialization (a shared volume, a real-session e2e), put `concurrency:` on the **expensive job** instead: the run always starts, decide and the reporter always execute, and a superseded run surfaces as a definitive red. Opt a deliberately-serialized non-required workflow out with `# static-concurrency-ok: <reason>`.

## No conditional checks — a skipped prerequisite is a red, not a pass

**A check that skips itself when its prerequisite is absent and reports green is a lie.** When a check needs a capability to verify something (a device node, a running service, a credential), it must **require** that capability and **fail loud** when it is missing — never `echo "skipped"; exit 0`, never a `skipif` that turns can't-verify into a pass. A missing prerequisite is a red ("could NOT verify — provision a capable runner"), never a false green. If a capability truly cannot be provided on any runner, leave the check out of the required set and say so, rather than wiring a check that can only hang or go red.

Narrow carve-out: a guard skipping a scenario genuinely _inapplicable_ on this platform — a Linux-container e2e on macOS — is not-applicable, which is different from an under-provisioned runner.

**A conditional gate can also ship broken and stay green indefinitely.** A job that only runs on certain commits may never fire after being introduced, so the "green" you see is a skip, not a pass. When you add or change a conditional gate, trigger it once on a commit that meets the condition.

## A scheduled workflow must route its failures to a human

**A workflow triggered by `on.schedule:` has no PR surface, so its red is invisible** — no check run, no reviewer, nothing blocking a merge; the failure sits in an Actions tab nobody opens. Every scheduled workflow therefore takes one of three routes: (a) it is listed by display `name:` in [`ci-failure-notify.yaml`](workflows/ci-failure-notify.yaml)'s `on.workflow_run.workflows` — the default, and the one that needs no per-workflow YAML; (b) it calls [`notify-ntfy`](actions/notify-ntfy) on its own failure path, which is worth the duplication only when the alert needs a message or an urgency the shared one cannot give; or (c) it carries `# cron-alert: false  # <reason>` on its `schedule:` key or a direct-child line. A negative placeholder ("n/a", "not needed") is not a reason.

Two things make this more than a formality. **The notify step must be REACHABLE on failure** — in an `if: always()` job that inspects `needs.<job>.result`, never on `if: failure()` alone. **A job that exceeds its `timeout-minutes` is CANCELLED, not failed**, so `failure()` is false for the slowest and likeliest death of a long job; `cancelled()` covers it, and under `cancel-in-progress: false` a cancel can only be a timeout or a human. A notify step in a job that only runs on success is an inert feature wearing a fix's clothes.

## Cache every repeated download

**A step that downloads the same bytes on every run must restore them from `actions/cache` instead.** An uncached fetch runs at the registry's speed on the day, not yours, and the cost is paid once per job per run — so a 12-shard matrix pays it 12 times.

- **Key on the pinned version**, plus `runner.os` and any toolchain version the artifact is built against. A key that cannot change is a cache that never refreshes; a key that always changes is no cache.
- **Pin the version first.** A floating `latest` has no stable key.
- **Make the install idempotent, or gate it on `cache-hit`** — either the script checks the tool's own `--version` and exits early, or the step carries `if: steps.<id>.outputs.cache-hit != 'true'`.
- **Keep the bounded install on the miss path** (a retry wrapper plus `timeout --kill-after`). A cache is an optimization; the miss is a normal outcome, since GitHub evicts an entry after 7 idle days.
- **Never cache what the job exists to test.** An e2e verifying a real auto-install must keep hitting the network; cache the harness's prerequisites instead.

## Scoping work to a change range

- **A job scoping work to a PR's change range must derive the head from the checkout, not the event payload.** Resolve it with `git rev-parse HEAD` after `actions/checkout`, never `github.event.pull_request.head.sha` — the event SHA is frozen at trigger time, so a rebase or force-push points it at a diverged commit and silently mis-scopes the range to the whole branch history.
- **A formatter wired only into the staged-files commit hook leaves a hole the merge button drives through.** GitHub's squash and merge run no client hooks, so a 3-way merge can land formatting drift that a CI `format --check` then rejects on an otherwise-clean `main`. Wire the formatter into an all-files autofix step or CI job, so the check side and the fix side cover the same surface.

## Tokens and the API

- **`GITHUB_TOKEN` cannot resolve review threads in Actions.** `resolveReviewThread` returns "Resource not accessible by integration" for the app installation token even with `pull-requests: write`, while `addPullRequestReviewThreadReply` on the same thread succeeds. A bot that auto-resolves conversations needs a PAT (a user-actor token) for the resolve mutation.
- **`gh api --paginate --jq` applies the jq filter per page.** A filter ending in a reducer (`last`, `first`, `max_by`, `add`) is silently wrong across a page boundary — it runs the reducer on each page separately. Add `--slurp` so the pages merge into one array and the reducer runs once.
- **An explicit `permissions:` block sets every unlisted scope to `none`.** A step that lists workflow runs needs `actions: read`; one that reads a PR's draft state needs `pull-requests: read`. A missing scope answers 403, and a script that treats an API fault as "no data" then degrades silently instead of failing.

## Autofix workflows

- Trigger on `pull_request` directly, not `workflow_run` — with `workflow_run` the triggered job runs against the base branch (not the PR head), the log context must be fetched as an artifact, and the mismatch makes failures hard to diagnose.
- Gate on a non-bot actor (`github.event.pull_request.user.type != 'Bot'`) from day one. `claude-code-action` rejects bot-authored PRs, so the workflow burns CI minutes and accomplishes nothing.
- Don't ship a static "recoverable" allowlist (lint/format/docstring). It either duplicates pre-commit or needs human judgment about why a rule fires here; let the action decide whether a failure has a tractable mechanical fix.
- A two-stage "label it / act on it" automation needs matching eligibility predicates. When the labeler's filter is broader than the actor's, the label silently promises an action the actor refuses; keep both predicates identical, or have the labeler apply the actor's exclusions.
- An in-source annotation (a comment, a label, a tag) that two separate tools read has two meanings that diverge at the one file where the questions differ. Before adding a second reader for an existing marker, enumerate files where the two readings disagree; use two markers if any exist.
- **A workflow that cancels in-flight runs on PR close must filter by `createdAt < closed_at` and exclude its own `run_id`, not by head SHA alone.** GitHub dispatches `closed`-event runs on the same SHA as the PR, so a SHA-only canceller kills the close-handler jobs it is supposed to spare. For a job with a durable side effect (filing an issue, pushing a row), prefer a reconciler keyed on a content hash with a `schedule:` leg over a one-shot `closed` trigger; a lost run then converges on the next cycle.
- An automated repair loop that records each attempt in a ledger must distinguish "the run never started" from "the run reached a verdict." An infrastructure failure silently consumes the retry budget unless it writes a distinct outcome class. Key the retry decision on a class the code emits, not a substring of the free-text reason — a text match is a channel by which the supervised process can request its own retry.

## Lint ratchets

- **When a lint cap is disabled because of existing violations, replace it with a grandfathered ratchet, not silence.** Baseline the current violators, cap new ones, and fail stale entries so the list only shrinks. The flat cap fails at adoption (existing violators block unrelated work), but no cap is the worst outcome. The RuboCop-todo / pylint-todo shape works for any linter metric: file size, complexity, suppression counts.
- **Dogfood a new lint against the real tree before committing it.** If it fires on existing legitimate code, narrow the class or scope; adding an allow-list is the wrong move — a noisy guard gets disabled at adoption.
- **An allowlist entry is only as valid as the reference it points at.** A merge that rewrites the pointed-at path or symbol orphans the entry without a conflict marker; include the allowlist in any guard that checks for referenced-but-missing paths.
- **Guards that aggregate over the full post-merge tree fire only after the PR lands**, so a workflow or CI-script PR can silently break them. Run the affected guards locally before landing such a PR — they are easy to miss because they are not obviously related to the change.

## Every guard needs a watched surface

**Before calling a check done, ask "when this goes red, WHO SEES IT?"** A check is worth exactly what its failure surface is worth, and the surfaces are not equal: a required PR check blocks a merge and gets read, while a `schedule`, `workflow_dispatch`, or post-merge run has no PR surface at all — its red lands in an Actions tab nobody opens, so the check is decorative. Route the failure to a human (a notification action, an issue), or say why nobody needs to know.

**Then ask it about duration: "if this stayed broken for a month, what would tell me?"** A cron whose failures notify nobody is silent for exactly as long as it stays broken, and a month of that looks identical to a healthy quiet month. Its twin and the strictly worse case is the **vacuous green**: a check that fails to fetch its input, degrades to a placeholder, and reports success — no surface saves that, so pair the question with "what does this do when its input is missing?" and make the answer fail closed.
