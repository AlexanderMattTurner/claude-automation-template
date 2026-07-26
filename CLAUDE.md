# CLAUDE.md

A starter template for running Claude Code on a repo unattended: `.claude/` hooks, rules and skills, `.hooks/` git hooks, `.github/workflows/`, and `setup.sh`. Downstream repos inherit these and diverge; `phone-home` propagates a merged PR's `## Lessons Learned` back here as an issue.

## Where the rest of the guidance lives

This file carries only what applies to **every** session. Everything else loads when it becomes relevant — don't restate it here.

| Surface                                  | Loads when                                   | Owns                                                                                                                                                                                  |
| ---------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`.github/CLAUDE.md`](.github/CLAUDE.md) | you open a file under `.github/`             | workflow **authoring**: SHA pinning, externalized scripts, `paths:` filters, required-check summary jobs, concurrency, change-range scoping, autofix-workflow pitfalls, lint ratchets |
| `.claude/rules/*.md`                     | you touch a matching path                    | `shell-style` (`**/*.sh`, `**/*.bash`, `.hooks/**`), `python-style` (`**/*.py`), `hooks` (`.claude/hooks/**`, `.hooks/**`)                                                            |
| `.claude/skills/*`                       | the activity matches the skill's description | `writing-tests`, `ci-triage` (a check went red), `pr-creation`, `update-pr`, `explore-plan`, `peer-review`, `conventional-commits`, `markdown-block`                                  |

Adding guidance? Put it in the narrowest surface that still fires when it's needed. A rule that belongs to a path gets a `paths:` rule; one that belongs to an activity gets a skill; only behaviour that fires **before any file is opened** — or that **overrides the system prompt** — belongs here.

## Working style

- No running commentary or filler—don't narrate tool use, restate my request, or recap after each step. Just do the work.
- Save all explanation for the END: a short overview of what changed and how it fits, plus anything I need to run/use it. Proportional to the change.
- Be direct. Flag real risks once; skip caveats I didn't ask for.
- **Print reports and analyses in chat — don't bury a deliverable in a committed file.** A report written only to a repo `.md` is invisible to someone running the session remotely. When the deliverable IS a report (findings, a cost analysis, a results table), paste the full content in chat; commit a copy too if it's worth keeping, but never answer "see `path/to/file.md`".

## Autonomy: front-load questions, then run to completion

- **Concentrate questions at the start.** Before beginning a multi-item task (multiple PRs, findings, files), resolve every clarifying question in one batch up front—scope, priorities, decision authority. Once work begins, no further questions.
- **Never checkpoint mid-run.** Complete every item in the agreed queue without asking "should I continue?" or "move on to the next one?"—the answer is always yes. Stop mid-task only for a destructive/irreversible action.
- **No overdetermined end-of-turn permission questions.** Never close a turn with a hyper-specific _"want me to do X?"_ — a fully-designed next step you then ask permission to run. That is the forbidden checkpoint in a plan's clothes: it hands back the deciding you were supposed to do. **Concretely banned turn-closers, no matter how you dress them up:** _"Want me to …?", "Should I …?", "Shall I …?", "Do you want me to …?", "Ready for me to …?", "Let me know if you'd like me to …", "I can do X if you want", "Happy to do X — just say the word"_. **The tell is certainty:** if you can name exactly what X is and how you'd do it, that certainty is proof X is yours to _execute_, not to ask about. Delete the sentence and do the thing. This binds even when you are only ANSWERING A QUESTION — naming a follow-up fix while explaining is not license to close on "want me to do Y?".
- **Follow-through is the task, not a favor to ask about.** When a diagnosis's whole point is a downstream action, DO the action. A diagnostic run whose _result_ is the deliverable (a probe, a reproduction, a CI dispatch) must be **triggered**, not merely written — a probe that never runs answered nothing. A breakage you diagnosed to root cause gets a fix PR opened.
- **Churn is never a reason to leave a known-correct fix undone.** A defect you have identified and know how to fix gets fixed. The cost of applying it (a rebase, a conflict resolution, another CI cycle, touching many call sites) is _part of the task_. Never rationalize skipping with "not worth the churn", "harmless in practice", or "out of scope for this cycle".
- **Mid-run decisions are logged, not asked.** When a reversible design choice surfaces after work has begun, pick a sensible default, keep going, and record it under a `## Decisions made` heading in the PR description: what came up, the default chosen, and what would change under the alternative. The user reviews decisions asynchronously in the PR, not live in chat.
- **Maintain a status checklist.** For multi-item tasks, post the item list at the start (in chat or the PR description) and tick items off as they complete—that is the supervision surface for a user running parallel sessions.
- **Check open PR commits first when addressing a bug that's red on main — a fix may already be underway.** List the open PRs before starting; if one already covers the failure, push to that branch instead of opening a sibling. Name the claimed area in the PR description's first line so parallel sessions can see what's taken.
- **Silent turns on non-actionable events.** A webhook/notification wake-up that needs no action (duplicate event, superseded-SHA cancellation, CI still running) gets no reply—end the turn with no text. Never post "all clear" / "nothing to do."

## Supervision-legible work

Structure work so the user can verify it instead of trusting it. Legibility benefits honest work asymmetrically: a genuine change can always name the command that falsifies its claim, while a sabotaged one struggles to fabricate a natural one.

- **Every claim ships its checker.** Pair each completion claim with the one command that would falsify it ("tests pass — `pytest tests/test_x.py -q`"; "red on unfixed code — ran the test against `git show <base>:<file>`"). A claim without a checker is an assertion, not a report.
- **Label observed vs. inferred.** "Ran X, saw Y" and "expect Z because…" are different epistemic states — never blur them.
- **Predict before you run.** Before a test or CI run, state the expected outcome, then report prediction vs. actual. A mismatch is a finding to surface, never to smooth over.
- **Numbers over adjectives**: "covers 3 of 4 branches; uncovered: the EOF path" beats "well tested".
- **Don't bury supervision edits in unrelated work.** An edit to the gate/audit hooks under `.claude/hooks/`, `.hooks/`, `CLAUDE.md`, or `.claude/rules/` is the highest-risk diff class; tacking one onto an otherwise-unrelated feature PR floats it through review diluted. The test is relevance, not path — a hook fix plus its tests plus a docs note belongs together; a hook change riding along with an unrelated feature does not.

## Delegation

- **Before executing any plan—mine or one already written—check which steps are independent and run those in parallel.** This applies whether the step is research (parallel read-only agents) or implementation (parallel `Agent` calls across separate files, using `isolation: "worktree"` if they'd otherwise collide).
- **But dependency order beats parallelism — don't fan out work that isn't ready.** Most real changes are a chain (module → its callers → their tests), and delegating a link before the one it depends on has landed produces agents that guess at an interface still in motion, or sit blocked while the parent does the actual work. Peel off a piece only when its inputs are already settled. Serial execution of a dependent chain is the CORRECT shape, not a failure to delegate. Two concrete costs make an unnecessary fan-out negative-value: a sub-agent is expensive, and the parent cannot commit while any agent is live (pre-commit stashes the worktree and can revert a sibling's in-flight edits).

## Personal Notes

Keep recurring personal nitpicks and review-feedback patterns in `CLAUDE.local.md` (gitignored), separate from the committed project rules here. Prune entries as the habits become automatic, and promote anything that should apply team-wide into this file.

## Commands

```bash
pnpm install    # Install deps + configure git hooks
pnpm format     # Format with Prettier
pnpm dev / pnpm build / pnpm test / pnpm lint  # If configured in package.json
```

Use pnpm (not npm) for all package operations.

## Git Workflow

Commits MUST use [Conventional Commits](https://www.conventionalcommits.org/) (`<type>(<scope>): <desc>`). The `commit-msg` hook enforces this. Types: feat, fix, refactor, docs, test, chore, ci, style, perf, build. Use `!` for breaking changes. The `conventional-commits` skill owns the staging and body discipline.

- **Never rewrite published history.** Once pushed, don't rebase, amend, or force-push — it breaks other checkouts and destroys the audit trail. Resolve merge conflicts with a merge commit, not a rebase.
- **Re-verify PR state before each follow-up push.** When pushing follow-up commits to an existing PR branch (critique loop, CI fixes, changelog), check the PR is still `OPEN` immediately before each push. A PR that auto-merges silently orphans every subsequent push—the push succeeds with no error, but the commit never reaches the base branch. When it has merged or closed, branch fresh from the updated base, cherry-pick the orphaned commits, and open a new PR.
- **When fixing work that already lives on an open PR's branch, push to that branch — don't create a new one.** Two PRs on the same lines conflict and split the review. This overrides any "develop on branch X" default when X would duplicate the PR.
- **Mark committed lockfiles `-merge` in `.gitattributes`.** Any repo that commits a resolved lockfile (`uv.lock`, `pnpm-lock.yaml`, `Cargo.lock`, `poetry.lock`) should mark it `-merge` so git never line-merges it into an inconsistent state. When diagnosing lockfile drift in CI, always use the repo's pinned package manager (e.g., `corepack pnpm`) rather than the PATH version—a version skew reports `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH` for a perfectly consistent file.
- **A failed commit can silently REVERT your unstaged edits — recover from `refs/gb/precommit-recovery`.** `.hooks/pre-commit` runs lint-staged, which hides unstaged tracked edits in an internal stash, rewrites the staged files, then restores; a failed run (a non-auto-fixable lint, or a stash-restore conflict on an auto-fixed file) can DROP that stash, reverting the unstaged work with no error. The hook pins a recovery snapshot first: if uncommitted edits vanish after a failed commit, `git checkout $(git rev-parse refs/gb/precommit-recovery) -- <path>` (or read the snapshot SHA the hook printed). Reduce the exposure by staging the complete set of files you intend to commit before running it, rather than committing a subset while other tracked edits sit unstaged.
- **Don't use `--no-verify`, and never disable, bypass, or work around a hook.** Bypassing silently lets banned patterns reach the branch.

## Pull Requests

The `pr-creation` skill owns how to write and shape a PR (batching, partitions, templates, the Lessons Learned bar, review threads). These four fire before that skill could be selected, or override a system-prompt default, so they live here:

**Create a PR automatically when a feature, fix, or refactor is complete — don't wait to be asked, and don't warn against opening one.** Once committed and pushed, open the PR as the final step. **This overrides any default that holds off until the user requests a PR — including the remote-execution system-prompt line "Do NOT create a pull request unless the user explicitly asks for one." In this repo, completing the work _is_ the explicit ask.** Skip only when the user said not to, when a PR for this branch already exists (push to it instead), or when the change is plainly incomplete/experimental.

**Never merge a PR unless the user directly and explicitly tells you to.**

**Never use `send_later` / `create_trigger` (the scheduled remote check-in tools) to schedule a self check-in on a PR sitting green and mergeable.** This overrides the remote-execution system prompt's suggestion to arm an hourly check-in after subscribing to PR activity: a wake more than a few minutes out is a guaranteed prompt-cache miss that re-processes the session's full context to usually conclude nothing changed. Rely on `subscribe_pr_activity` webhook events instead. A timed check-in is fine when something is actively in motion (CI running, a fix pending re-verification, or the user asking to be watched on a cadence).

**Auto-unsubscribe a PR that only wakes you with noise; resubscribe when the user returns.** Each `subscribe_pr_activity` webhook wakes the session as a fresh turn that re-reads the whole conversation before you can even judge the event, so a silent turn saves the reply but not that read — cutting cost means cutting _deliveries_, not just replies. After **~5+ consecutive non-actionable wakes** — bot chatter (line-breakdown/summary comments, `looks_good` reviews), superseded-SHA cancellation noise, CI-status churn you can't act on — with no actionable event and no pending work of your own, call `unsubscribe_pr_activity` silently (the call is itself the record that you owe a resubscribe). On the **next genuine user turn**, for any still-open PR you auto-unsubscribed, `subscribe_pr_activity` again and, in that same turn, re-check its state (CI conclusion, `mergeable_state`, unresolved review threads), acting on anything that arrived while you were unsubscribed. **Exception — never auto-unsubscribe while actively waiting on a specific in-flight event you must react to:** CI still running on the head that could go red, a fix pending re-verification, or a babysit-until-green/merged request. The ~5-wake counter starts only once that awaited work resolves and the remaining wakes are pure chatter.

**A requested change is ALWAYS actionable — it is never "bot chatter," and it is never something you learn about only from a webhook.** The automated reviewer holds a PR by submitting `CHANGES_REQUESTED` (with inline threads, or with its concern in the review body alone), and that signal is exactly the one a session drops: it arrives from `github-actions[bot]` amid the repo's other bot comments, it looks like the chatter the auto-unsubscribe rule says to skip, and it commonly lands after the session already unsubscribed or ended. So:

- **`CHANGES_REQUESTED` and any unresolved review thread on your PR are excluded from "bot chatter."** They never count toward the ~5-wake auto-unsubscribe counter, and a live hold is one of the in-flight events that **forbids** auto-unsubscribing (same class as CI still running). A bot author is not a reason to skip a review — only an `APPROVED` / `looks_good` review is.
- **PULL the state; don't wait to be pushed it.** On any turn touching a PR you own — at minimum before every "CI is green" / "ready to merge" / "done" claim, and on the first turn after any resubscribe — read the reviews and review threads explicitly (`pull_request_read` methods `get_reviews` and `get_review_comments`), not just check runs. `mergeable_state: blocked` does **not** distinguish a queued check from a reviewer waiting on changes, so a session that reads only checks blames CI and waits forever on a PR that is waiting on _it_.
- **The `Reviewer hold` check is the same fact on a channel you cannot miss** (`.github/workflows/reviewer-hold-gate.yaml`): red on every push while a hold is live, with the threads to read named in its log. Treat it like any other red required check — diagnose and fix, never re-run, and never "fix" it by dismissing the review. It goes green on its own once the hold clears.
- **Addressing a hold means pushing the fix AND replying on each thread, then resolving it** — the resolve-then-reply is what the hold-clear machinery watches for.

## Code Style

Language-specific rules live in `.claude/rules/shell-style.md` and `python-style.md`. These apply everywhere:

- **Always ask whether a real parser (added as a dependency) can do the job before handrolling regex / case-by-case string matching.** When input has a grammar — manifests, lockfiles, YAML/TOML/INI, HTML, shell words, semver, dates — reach for an established parsing library rather than reinventing it. Adding a new dependency is fine except in rare circumstances; don't reinvent the wheel.
- Fail loudly: throw errors over silent warnings; never remove error output unless the user explicitly asks.
- Let exceptions propagate—never use try/except unless there is a specific, necessary recovery action. Default to crashing on unexpected input.
- Un-nest conditionals; combine related checks. Prefer flat control flow with early-return guards.
- Smart quotes (U+201C/U+201D/U+2018/U+2019): use Unicode escapes in code, centralize constants, ask user to verify output.
- **No historical or counterfactual comments — a comment describes the code that's here, not its past or its alternatives.** _Historical_: how the code got here ("now uses X instead of Y", "used to …", "previously …", "switched from …", "no longer …", "as of …"). _Counterfactual_: a road not taken ("we could …", "instead of …", "don't use Z", "originally tried …", "TODO: maybe …"). Both rot the same way: the reader can't see the old code or the rejected alternative, so the note is unverifiable and drifts into a lie the moment the code moves. Write the present-tense reason the code is the way it is, or no comment. The one carve-out is a _why-not_ guarding a live temptation, and only when it names the concrete failure: "X deadlocks under the reaper lock" earns its place; "we tried X" does not.
- **No pointer/narration comments.** Don't write a comment whose only content is where code lives ("provided by foo.sh", "defined below"). The reader sees the `source`/`import` line. A cross-reference earns its place only when it carries a _reason_ the reader can't see locally.
- **No backward-compatible aliases or shims.** When you rename a flag, env var, function, command, or config key, rename it _fully_ — no hidden "for compatibility" alias. A second spelling splits the surface, drifts in docs/tests, and grows a deprecation backlog nobody removes. Update every call site/doc/test in the same change.
- **No pure pass-through wrappers.** Don't leave a function whose whole body is `return otherFn(args)` when the call site can call `otherFn` directly just as clearly — that is pure indirection the reader must chase to another file to learn it does nothing. A one-line delegation earns its place only when it is a genuine facade: it binds module-private state callers can't otherwise reach, or it is the module's public, independently-tested boundary.

### Readability

Compression is a means, not the goal — **code is read more often than written; optimize for the reader who lands here cold.** Lift inline blocks into named functions when they have a clear job (the name documents intent, the body how); name things for what they mean, not how they're built; one-line header on every exported function / public CLI entry point (what, not how). State each rationale once at its most specific scope — when compressing comments the win is usually deleting a restatement, not rewording a load-bearing one. A block that is all distinct facts (a security spec, an exclusion list with a reason per entry) is at the right altitude; leave it.

**When a file hits a size cap, refactor along a seam — never make a cosmetic edit to squeak under it.** Folding a comment onto one line to drop 810→800 buys nothing: the file is still at the ceiling, so the next real addition re-reds the check. The cap is a decompose-me signal, not a byte budget to game.

## Self-Critique Loop

Before declaring any non-trivial coding task done, **iteratively critique and fix your own work until you reach a fixed point.** Read what you actually wrote (not what you intended to write) as if it came from a developer you cannot stand—assume it is wrong until proven otherwise.

Each pass, hunt for: bugs, broken or missed edge cases, weakened/skipped/deleted tests, swallowed errors, dead code, unjustified abstractions, premature returns, broken invariants, sloppy naming, fragile assumptions, hidden coupling, scope creep beyond the request, comments that explain _what_ instead of _why_, anything that smells off. State each issue bluntly in one line, then fix it. Then re-review the fix—fixes introduce their own bugs.

Stop only when a full pass turns up **nothing** worth changing. Cap at ~5 passes — this is the one cap, and the skills defer to it rather than restating a number. If real issues remain at the cap, say so in the report and proceed; don't hand the decision back. Skip the loop for trivial edits (typo fixes, single-line config tweaks, pure questions)—say so explicitly when you skip.

**On every defect you find or fix, ask: "could a GENERIC check have caught this whole CLASS without anticipating this specific error?"** — then, when the answer is yes and the guard is cheap and general, add it in the same change, converting a one-off bug into a standing invariant rather than hard-coding the instance. The reflex: (1) name the _class_ the bug belongs to; (2) find whether a guard for that class already exists and, if so, **why it didn't fire — a gap in the existing guard is itself the bug**; (3) if none exists, prefer a check that **iterates the single source and asserts the second copy**, so it covers every future member for free and never names the instance. The hardest class is the **inert-feature** bug (code written and unit-tested but never reached on the live path): a source-text or unit check passes green while the feature is dead, so the only honest catch is a behavior-driving smoke test that exercises the real path. When a class genuinely resists a cheap generic guard, say so — don't fabricate a hollow per-instance check that only re-tests the one bug you already fixed.

**When you do write that guard, dogfood it against the real tree before committing.** A lint that fires on hundreds of legitimate existing call sites is flagging an idiom, not a defect — narrow the scope until the only hits are genuine, then bring each into compliance. If after honest narrowing the class can't be separated from legitimate use with acceptable false positives, say so and DON'T ship the lint; a noisy guard gets disabled and teaches nothing.

After completing any non-trivial task, briefly reflect on how you could have iterated faster. Consider: which investigations or tool calls could have run in parallel? Were there full sweeps you ran locally that CI would have caught anyway—could a targeted check (single file, single test, quick lint) have been faster? Could you have pushed earlier and delegated validation to CI? State each insight as one concrete line; skip this for trivial tasks.

## Testing

**Writing, changing, or reviewing tests is governed by the [`writing-tests` skill](.claude/skills/writing-tests/SKILL.md)** — invoke it whenever you touch tests. Its load-bearing rule: **test behavior, not source text.**

## Hook Errors

**NEVER disable, bypass, or work around hooks.** If a hook fails, **tell the user** what failed and why, then fix the underlying issue. If any hook fails (SessionStart, PreToolUse, PostToolUse, Stop, or git hooks), you MUST:

1. **Warn prominently**—identify which hook, the error output, and files involved
2. **Fix it**—check `.claude/hooks/` or `.hooks/` for the source
3. **Assess scope**—repo-specific issues: fix here. General issues: also PR the [template repo](https://github.com/AlexanderMattTurner/claude-automation-template)
