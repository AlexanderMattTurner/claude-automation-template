#!/usr/bin/env bash
# Install @anthropic-ai/claude-code globally, pinned to the version this repo's
# own package.json devDependencies names — so the CLI a CI job runs and the CLI a
# developer installs from the lockfile are the same build.
#
# Reads ./package.json, so run it with the repo root as the current directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/retry.bash disable=SC1091
source "$SCRIPT_DIR/lib/retry.bash"

version="$(node -p "require('./package.json').devDependencies['@anthropic-ai/claude-code']")"
if [[ "$version" == "" ]] || [[ "$version" == "undefined" ]]; then
  echo "could not read @anthropic-ai/claude-code version from package.json" >&2
  exit 1
fi
echo "Installing @anthropic-ai/claude-code@${version}"
# Bound + retry: a bare `npm install -g` has no timeout, so a hung registry
# connection (intermittent on GitHub egress) would stall here until the whole
# job's timeout cancels it. `timeout` caps a stuck attempt; retry_cmd rides out a
# transient blip rather than failing the run.
retry_cmd 3 10 timeout 180 npm install -g "@anthropic-ai/claude-code@${version}"
claude --version
