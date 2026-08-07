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
