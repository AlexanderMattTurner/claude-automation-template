#!/usr/bin/env bash
# kcov-exclude: a GitHub Actions step body that no test runs: it reads the runner's context
#   — GITHUB_*, a job-scoped GH_TOKEN, an actions/ working directory — or provisions the
#   runner itself, so it has no entry point off a runner.
# Install @anthropic-ai/claude-code globally at the RESOLVER's pin.
# Env: NPM_INSTALL_TIMEOUT_SECONDS, NPM_INSTALL_KILL_AFTER_SECONDS,
# NPM_INSTALL_RETRY_DELAY_MS tune the bound below.
set -euo pipefail

# The pin comes from this script's OWN repository, never from the working
# directory. Both callers `cd` somewhere first — the self-review into the calling
# repository's base, the conflict resolver into this one — and a version read
# from the caller's tree would let a repository this resolver merges for choose
# which CLI binary runs the merge.
_resolver_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_pin_file="${_resolver_root}/.github/claude-cli-version"
if [[ ! -f "$_pin_file" ]]; then
  echo "no ${_pin_file}: the resolver has no pinned claude-code version to install" >&2
  exit 1
fi
version="$(tr -d '[:space:]' <"$_pin_file")"
if [[ -z "$version" ]]; then
  echo "${_pin_file} is empty; it must hold one @anthropic-ai/claude-code version" >&2
  exit 1
fi
# Idempotent: a claude already at the pin needs no install. This is what makes
# the install-claude-cli action's cache restore work — a restored global tree
# answers with the pinned version and the registry is never contacted.
if [[ "$(claude --version 2>/dev/null || true)" == "$version"* ]]; then
  echo "@anthropic-ai/claude-code@${version} already installed; skipping"
  exit 0
fi
echo "Installing @anthropic-ai/claude-code@${version}"
# Bound + retry: a bare `npm install -g` has no timeout, so a hung registry connection
# stalls here until the job's own timeout cancels it. --kill-after is what makes the cap
# real: `timeout` alone sends only SIGTERM, and an npm blocked on a dead registry socket
# takes minutes to act on it. The ladder must fit INSIDE the tightest caller's budget —
# pytest-checks.yaml's pytest-shard leaves about 5 min, and this spends 310 s worst case.
# shellcheck source=.github/resolver/lib/retry.bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/lib" && pwd)/retry.bash"
gb_retry --name "the claude-code npm install" --attempts 2 \
  --delay-ms "${NPM_INSTALL_RETRY_DELAY_MS:-10000}" -- \
  timeout --verbose --kill-after="${NPM_INSTALL_KILL_AFTER_SECONDS:-30}" \
  "${NPM_INSTALL_TIMEOUT_SECONDS:-120}" \
  npm install -g "@anthropic-ai/claude-code@${version}"
claude --version
