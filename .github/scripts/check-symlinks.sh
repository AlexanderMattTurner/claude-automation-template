#!/usr/bin/env bash
# Reject tracked symlinks whose target is an absolute path (e.g.,
# `/Users/foo/...`) — they silently break on every machine but the author's.

set -euo pipefail

# INVARIANT — this guard never passes on a git failure: it reports a required
# check, and an empty listing reads as "no symlinks here". Capturing lets git's
# status abort the script, where a `< <(git …)` loop swallows it.
entries="$(git ls-files -s -z | tr '\0' '\n')"
violations=""
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  mode=$(printf '%s' "$line" | awk '{print $1}')
  hash=$(printf '%s' "$line" | awk '{print $2}')
  path=$(printf '%s' "$line" | cut -f2-)
  [[ "$mode" = "120000" ]] || continue
  target=$(git cat-file blob "$hash")
  # Filter — only an absolute-path target is a violation; a relative symlink
  # target is portable and correctly left alone.
  # case-default-ok: no-match is the intended no-op, not a missed case.
  case "$target" in
  /*) violations="${violations}${path} -> ${target}"$'\n' ;;
  esac
done <<<"$entries"

if [[ -n "$violations" ]]; then
  echo "::error::Tracked symlinks resolve to absolute paths (not portable across machines):"
  printf '%s' "$violations"
  exit 1
fi
