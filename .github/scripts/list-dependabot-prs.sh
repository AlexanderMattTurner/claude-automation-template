#!/usr/bin/env bash
# Emit a multi-line GITHUB_ENV variable (DEPENDABOT_PRS) describing open
# dependabot PRs so a downstream "triage" step can subsume them.
#
# Inputs (env):
#   GH_TOKEN       GitHub token for `gh`
#   GITHUB_ENV     Path to GitHub Actions env file (optional outside CI)

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN must be set}"
GITHUB_ENV="${GITHUB_ENV:-/dev/null}"

sentinel="PR_EOF_$(uuidgen)"
if ! listing=$(gh pr list \
  --state open \
  --search "author:app/dependabot" \
  --json number,title,headRefName,headRefOid,url \
  --jq '.[] | "- #\(.number) [\(.headRefName)@\(.headRefOid[0:7])] \(.title) — \(.url)"'); then
  listing="_Failed to list dependabot PRs — investigate before relying on subsume step._"
fi

{
  echo "DEPENDABOT_PRS<<${sentinel}"
  printf '%s\n' "${listing}"
  echo "${sentinel}"
} >>"$GITHUB_ENV"
