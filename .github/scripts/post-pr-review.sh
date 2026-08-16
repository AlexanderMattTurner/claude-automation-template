#!/usr/bin/env bash
# Post the review agent's structured findings as ONE GitHub PR review with
# inline, line-anchored comments and (where offered) one-click suggested edits.
# post-pr-review.mjs builds the reviews-API payload from review.json; this posts
# it. APPROVE (and occasionally REQUEST_CHANGES) can 422 under GITHUB_TOKEN when
# the repo does not allow Actions to cast a formal vote — observed: APPROVE
# always rejected here, COMMENT always accepted. Retried as COMMENT first, since
# review-gate.sh counts any undismissed review regardless of event; only a
# COMMENT rejection (e.g. a genuinely bad anchor) falls back to a plain comment.
#
# Requires: gh authenticated (GH_TOKEN), GH_REPO, PR, PR_INPUT_DIR; node with the
# scripts on the module path. HEAD_SHA (the PR head sha) is optional but pins the
# review to the reviewed commit.
set -euo pipefail

: "${PR:?PR number required}"
: "${GH_REPO:?GH_REPO required}"
: "${PR_INPUT_DIR:?PR_INPUT_DIR required}"

# A non-zero exit from the reader means the reviewer produced no valid
# review.json — it crashed before writing its verdict. Surface that as a RED step
# (fail loud) rather than the reader's old silent green, so a broken reviewer
# can't masquerade as a clean pass. `if !` suspends set -e for the substitution so
# we can react to the failure instead of dying on it.
if ! status="$(node .github/scripts/post-pr-review.mjs)"; then
  echo "::error::the reviewer wrote no valid review.json — it likely crashed; see the reader's diagnostics above" >&2
  exit 1
fi
if [[ "$status" != "PAYLOAD" ]]; then
  echo "no structured review to post" >&2
  exit 0
fi

PAYLOAD="${PR_INPUT_DIR}/review-payload.json"

post_review() {
  gh api -X POST "repos/${GH_REPO}/pulls/${PR}/reviews" --input "$1" >/dev/null
}

if post_review "$PAYLOAD"; then
  echo "posted structured review with inline comments" >&2
  exit 0
fi

event="$(jq -r '.event' "$PAYLOAD")"
if [[ "$event" != "COMMENT" ]]; then
  echo "::warning::reviews API rejected a ${event} review; retrying as COMMENT" >&2
  comment_payload="$(mktemp)"
  trap 'rm -f "$comment_payload"' EXIT
  jq '.event = "COMMENT"' "$PAYLOAD" >"$comment_payload"
  if post_review "$comment_payload"; then
    echo "posted structured review as COMMENT (original event ${event} was rejected)" >&2
    exit 0
  fi
fi

echo "::warning::reviews API rejected the structured review; posting a summary comment instead" >&2
gh pr comment "$PR" --body-file "${PR_INPUT_DIR}/review-summary.txt"
