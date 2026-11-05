#!/bin/bash
# Run ruff at the version .pre-commit-config.yaml pins, the one CI enforces.
#
# PROBLEM CLASS — a local gate running whichever ruff is on PATH. A newer ruff
# enables more default rules, so it rejects a commit or a push of a tree CI
# passes. lint-staged and .claude/hooks/pre-push-check.sh both call this file.
# With no pin or no uvx it falls back to the PATH ruff. Bash builtins only: the
# hook PATH may hold no awk or sed.
#
# Usage: ruff.sh <ruff arguments>
set -euo pipefail

config="$(cd "$(dirname "$0")/.." && pwd)/.pre-commit-config.yaml"
pin=""
if [[ -f "$config" ]]; then
  in_ruff=false
  while IFS= read -r line; do
    if [[ "$line" =~ repo:[[:space:]]*(.*) ]]; then
      in_ruff=false
      [[ "${BASH_REMATCH[1]}" == *astral-sh/ruff-pre-commit* ]] && in_ruff=true
    elif $in_ruff && [[ "$line" =~ rev:[[:space:]]*[\"\']?v?([0-9][0-9.]*) ]]; then
      pin="${BASH_REMATCH[1]}"
      break
    fi
  done <"$config"
fi

if [[ -n "$pin" ]] && command -v uvx >/dev/null 2>&1; then
  exec uvx "ruff@${pin}" "$@"
fi
exec ruff "$@"
