#!/usr/bin/env bash
# Install @anthropic-ai/claude-code globally, pinned to the version
# .github/claude-cli/package.json names — one file, so every job that reaches
# for the CLI runs the same build, and Dependabot's npm updater bumps it.
#
# That package.json is not a pnpm workspace member: nothing here imports the
# CLI, and listing it in the root package.json makes `pnpm install` refuse the
# whole workspace over its unapproved install scripts (ERR_PNPM_IGNORED_BUILDS).
#
# The pin is consumer-owned: template-sync does not deliver it, because each
# repo's own Dependabot bumps it. Reads it relative to this script, so the
# caller's current directory does not matter.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/retry.bash disable=SC1091
source "$SCRIPT_DIR/lib/retry.bash"

# allow-unsynced: .github/claude-cli/package.json — consumer-owned; each repo's Dependabot bumps it.
pin_file="${SCRIPT_DIR}/../claude-cli/package.json"
# jq's own error (missing file, bad JSON) lands in $version so the refusal shows it.
# allow-exit-suppress: the regex check below rejects any $version that is not x.y.z, jq's error text included.
version="$(jq -r '.dependencies["@anthropic-ai/claude-code"]' "$pin_file" 2>&1)" || true
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "could not read an exact @anthropic-ai/claude-code version from .dependencies in ${pin_file}, got '${version}'." >&2
  echo "Each repo owns that file: create it as {\"private\":true,\"dependencies\":{\"@anthropic-ai/claude-code\":\"x.y.z\"}}." >&2
  exit 1
fi
echo "Installing @anthropic-ai/claude-code@${version}"
# Bound + retry: a bare `npm install -g` has no timeout, so a hung registry
# connection (intermittent on GitHub egress) would stall here until the whole
# job's timeout cancels it. `timeout` caps a stuck attempt; retry_cmd rides out a
# transient blip rather than failing the run.
retry_cmd 3 10 timeout --kill-after=10 180 npm install -g "@anthropic-ai/claude-code@${version}"
claude --version
