#!/usr/bin/env bash
# Fail-closed decide helper for CI jobs gated on package.json scripts.
#
# For each script name argument, writes `<name>_configured=true|false` to
# $GITHUB_OUTPUT (stdout when unset, for local runs). "false" is emitted ONLY
# for a script that is genuinely absent or still the template placeholder; a
# package.json that cannot be classified (malformed JSON, jq failure)
# propagates script-configured.sh's exit >=2 and fails the step — an
# unreadable manifest must go red, never skip-to-green a required check.
#
# Usage: decide-script-configured.sh <script-name> [<script-name>...]

set -euo pipefail

[[ $# -ge 1 ]] || {
  echo "usage: decide-script-configured.sh <script-name> [<script-name>...]" >&2
  exit 2
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for name in "$@"; do
  rc=0
  bash "$script_dir/script-configured.sh" "$name" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    configured=true
  elif [[ "$rc" -eq 1 ]]; then
    configured=false
    echo "::notice::'$name' script not configured, skipping"
  else
    # script-configured.sh already printed the loud diagnostic.
    exit "$rc"
  fi
  echo "${name}_configured=$configured" >>"${GITHUB_OUTPUT:-/dev/stdout}"
done
