#!/usr/bin/env bash
# Post the automated-review gate's verdict as a COMMIT STATUS on the PR head.
#
# PROBLEM CLASS — auto-merge landing a pull request before the reviewer has
# spoken. The cheap checks finish in about ninety seconds while an LLM review
# takes minutes, so a PR whose ruleset lists only the cheap checks merges first
# and the reviewer's REQUEST_CHANGES arrives on a merged PR. Nothing is red; the
# review simply was not part of the merge gate.
#
# The predicate is one line and stateless: a head is clear when at least one
# review exists FOR THAT HEAD. It needs no memory of which reviews have been
# seen, and it re-derives the same answer on every event.
#
# A COMMIT STATUS, not this job's own check run. Under `pull_request_target` the
# job's check run is reported against the BASE commit, so it never satisfies a
# requirement evaluated on the pull request's head. A status posted explicitly on
# `HEAD_SHA` does.
#
# Can't-verify is RED, never green: an API failure propagates through `set -e`,
# because a gate that fails open lets a PR merge past a review nobody read.
#
# Env: GH_TOKEN, GH_REPO (owner/name), PR, HEAD_SHA, RUN_URL.
set -euo pipefail

: "${GH_REPO:?GH_REPO required}"
: "${PR:?PR number required}"
: "${HEAD_SHA:?HEAD_SHA required}"
: "${GH_TOKEN:?GH_TOKEN required}"

# MUST stay byte-identical to the `name:` of the job in review-gate.yaml: that
# job name is what sync-required-checks registers as the ruleset's required
# context, and the status posted here has to carry the same context or the head
# never satisfies it.
GATE_CONTEXT="Automated review posted"

# Every review on the PR, paginated: a long-lived PR accumulates more than one
# page, and the review that clears this head can sit on any of them.
reviews="$(gh api --paginate "repos/${GH_REPO}/pulls/${PR}/reviews" \
  --jq '.[] | [.commit_id, .state, (.user.login // "")] | @tsv')"

# A review of THIS head, by anyone. The reviewer's own review clears it; so does
# the approval the auto-approve job posts for a PR the reviewer skipped by title
# or author — reading that outcome rather than re-deriving the skip predicate,
# which would be a second copy of decide-pr-review-trigger.sh's rules.
if reviewer="$(awk -F'\t' -v sha="$HEAD_SHA" '$1 == sha {print $3; exit}' <<<"$reviews")" &&
  [[ -n "$reviewer" ]]; then
  state=success
  description="Reviewed by ${reviewer}"
else
  state=pending
  description="Waiting for the automated review of this commit"
fi

# `pending`, not `failure`, for the not-yet-reviewed case: the review is coming,
# and a red would tell a reader to go diagnose something. Both hold the merge.
gh api -X POST "repos/${GH_REPO}/statuses/${HEAD_SHA}" \
  -f "state=${state}" \
  -f "context=${GATE_CONTEXT}" \
  -f "description=${description}" \
  -f "target_url=${RUN_URL:-}" >/dev/null

echo "posted ${state} status '${GATE_CONTEXT}' on ${HEAD_SHA}: ${description}" >&2
