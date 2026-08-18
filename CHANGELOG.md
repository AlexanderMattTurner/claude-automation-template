# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to
adhere to [Semantic Versioning](https://semver.org/).

Add user-facing changes under `## Unreleased` as you make them. On each push to
the default branch, `auto-version.yaml` publishes to npm and promotes the
`## Unreleased` block into a new dated `## [version]` section below it (see
`.github/scripts/version-bump.sh`); when `## Unreleased` is empty, Claude drafts
the prose from the release's commits.

## Unreleased

### Added

- `Automated review posted`, a **required** check that makes auto-merge wait for
  the automated reviewer. The cheap checks finish in about ninety seconds while
  an LLM review takes minutes, so a PR gated only on the cheap checks merged
  first and the reviewer's `REQUEST_CHANGES` landed on a PR that had already
  merged. `review-gate.yaml` posts the verdict as a commit status on each PR
  head: green once an undismissed review of the PR exists, `pending` before that,
  and `pending` again if the last review is dismissed. The context name is
  registered by a never-firing job in `review-gate-context.yaml`, because a job
  sharing the name would report its own green check run under the same context
  and satisfy the gate while the status still said `pending`.

- PreToolUse skill gates: opening a PR, writing a test file, or writing a plan is
  denied until the session has invoked `pr-creation`, `writing-tests`, or
  `explore-plan`. The rules already said to invoke them; the gates are what makes
  a session that skimmed past the rule notice. Each covers the CLI route and the
  MCP route, since locking one door only moves the session to the other, and each
  fails OPEN — an unusable session id or an unparsable payload costs the reminder,
  never the tool call.

- `shell-targets`, a decide-gate input that DERIVES a gate's watched paths from
  the shell entry point the job runs, instead of restating them in `paths-regex`
  where the copy drifts silently. `.github/scripts/shell-run-closure.py` walks
  every in-repo file the entry point can reach, following the paths it EXECUTES
  as well as those it sources, and reaching a target written as
  `"$root/path/to/x.sh"` through the token's path suffix. It over-approximates
  on purpose, so it combines with `paths-regex` rather than replacing it.
- Three skills ported from the downstream `agent-glovebox` tree: `git-workflow`
  (commit/push mechanics, who owns a merge conflict, auditing a bot's merge
  delta), `babysit-prs` (watch sets, mergeability and merge-queue state,
  re-arming auto-merge, which wake-ups deserve a reply), and `defect-to-guard`
  (turning a defect class into a guard PROPOSAL, and the arithmetic it must
  show). `CLAUDE.md` now points at them instead of carrying their rules inline.
- `.claude/rules/code-style.md`, which loads with any source file and carries the
  cross-language rules that used to sit in `CLAUDE.md` — plus asking the tool
  that owns a format, deleting a reimplementation once its replacement lands,
  "a change that makes a defect rarer is not a fix", the comment-block cap, and
  the no-drift-guard rule.
- A `Writing` section in `CLAUDE.md` governing every word a session produces, and
  an `End-of-session handoff` section covering what a session could not fix.
- The `decide` reusable workflow diffs the change range itself instead of calling
  `dorny/paths-filter`, and gains the inputs that go with it: `paths-regex`,
  `paths-regex-file` (an SSOT a local git hook can source too), `pytest-targets`
  (watched paths derived from a test's own import lines), `trigger-keyword`,
  `keyword-scope`, `skip-on-draft`, `ignore-comment-only-changes`,
  `shell-targets`, and `memoize-anchor-jobs`. It now gates `push`
  and `merge_group` events on their own ranges, re-anchors a stale webhook base
  to the live base tip so a merge commit stops over-triggering every gate, and
  fails loud on a gate configured with no trigger at all.
- A memo shadow on the decide job: `decide-memo-base.py` names the newest commit
  on the branch whose work job actually PASSED, and the gate logs what it would
  decide diffing from there. Logged only — nothing acts on it yet.

### Fixed

- Template-sync no longer introduces `auto-version.yaml` into a repo that does
  not already have it (new `OPT_IN_PATHS` mechanism). A consumer with its own
  release workflow used to end up with two publishers on the default branch;
  their concurrency groups differ, so both computed the same semver bump and the
  loser died on an `npm error code E404 … PUT` that named no duplicate. Adopting
  the workflow is copying the file in once; opting out is deleting it.
- `version-bump.sh` recognizes losing that race instead of failing on it: it
  skips when the version's tag is already on the remote, and classifies a
  publish `E404` by re-probing the registry rather than by reading the message.
  A 404 on a version that is genuinely absent still fails loud.
- The release checkout accepts an optional `RELEASE_BYPASS_TOKEN` (an own-owner
  PAT registered as a ruleset bypass actor) and falls back to `GITHUB_TOKEN`, so
  a protected default branch no longer rejects the release commit and tag with
  GH013 and strands every release.

### Changed

- The template's own JavaScript is linted (`eslint.config.mjs`, wired into
  pre-commit) under the rule set a consumer running `eslint .` over its whole
  tree would apply. Template-owned files previously contributed dozens of errors
  to consumers' lint, blocking publishing where a release gates on it.

### Added

- `check-pipefail-sigpipe.py` pre-commit lint: under `set -o pipefail`, a pipe
  consumer that stops reading mid-stream (`head -N`, `grep -q/-l/-m`, `sed '5q'`)
  SIGPIPEs its still-writing producer, so the pipeline exits 141 and `set -e`
  aborts — on exactly the large inputs the cap exists for, and only on a slow
  enough machine to be invisible in local testing. Detection is a real bash AST
  (`tree-sitter-bash`), fires only in scripts that enable `pipefail`, and a
  provably-bounded producer opts out with `# sigpipe-ok: <reason>`.
- `drop-superseded-ci-events.mjs` UserPromptSubmit hook: when a subscribed PR
  delivers a red CI-failure webhook whose HeadSHA no longer heads any remote
  branch (a newer push already superseded that run), the turn is ended before
  the model runs instead of burning a full-context turn to conclude "ignore it".
  Fails open on any uncertainty (control-plane package unavailable during a cold
  start, unparsable payload, git unavailable, or the SHA still being a live head).
- Hooks now cross the agent boundary through the `agent-control-plane-core`
  package (added as a runtime dependency, provisioned by `session-setup.sh`'s
  existing `pnpm install`) via the new `.claude/hooks/lib-control-plane.mjs` and
  `lib-hook-io.mjs` helpers, so the Claude hook wire-format has one source of
  truth instead of being hand-rolled per hook.
