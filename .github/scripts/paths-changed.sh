#!/usr/bin/env bash
#
# Decide whether a path-gated CI job needs to run.
#
# Required checks must ALWAYS report a status. A workflow-level `paths:` filter
# breaks that: when no matching file changes, the whole workflow is skipped and
# its summary gate never reports, so a Required check hangs "pending" forever.
# Instead, the workflow runs unconditionally and its real job is gated on the
# output of this script (called from an always-running `decide` job).
#
# Usage: paths-changed.sh <git-pathspec> [<git-pathspec> ...]
#
# Environment:
#   BASE_SHA  commit to diff from (PR base sha, or the push "before" sha)
#   HEAD_SHA  commit to diff to   (PR head sha, or the push sha)
#
# Writes `run=true` or `run=false` to $GITHUB_OUTPUT. Fails OPEN (run=true)
# whenever the diff range cannot be determined, so a relevant change is never
# silently skipped.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "error: at least one git pathspec is required" >&2
  exit 2
fi

HEAD_SHA="${HEAD_SHA:?HEAD_SHA env var is required}"
BASE_SHA="${BASE_SHA:-}"

out="${GITHUB_OUTPUT:-/dev/stdout}"

run_job() {
  echo "run=true" >>"$out"
  echo "Decision: run (reason: $1)"
}

skip_job() {
  echo "run=false" >>"$out"
  echo "Decision: skip (no changed file matched the pathspecs)"
}

# New branch or deleted base reports an all-zero sha; there is nothing to diff.
if [ -z "$BASE_SHA" ] || [[ "$BASE_SHA" =~ ^0+$ ]]; then
  run_job "no base commit to diff against"
  exit 0
fi

if ! git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
  run_job "base commit ${BASE_SHA} not available locally"
  exit 0
fi

# Three-dot diff compares against the merge base, matching GitHub's own
# changed-files semantics for pull requests. Any non-zero exit (including diff
# errors) falls through to the run branch, preserving fail-open behaviour.
if git diff --quiet "${BASE_SHA}...${HEAD_SHA}" -- "$@"; then
  skip_job
else
  run_job "a changed file matched the pathspecs"
fi
