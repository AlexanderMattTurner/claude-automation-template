#!/usr/bin/env bash
# Decide whether the PR reviewer (claude-review.yaml's review job) should run for this
# pull_request_target event, emitting run=true/false AND the model to use to
# GITHUB_OUTPUT.
#
#   opened / ready_for_review — always review, on Opus: the first, thorough look
#     at a newly reviewable PR (a normal open, or a draft marked ready).
#   labeled — review on demand, on Opus, when the "needs-auto-review" label is
#     applied. The escape hatch the auto-approve message points at: a PR the
#     reviewer skipped by title/author (chore/style, or a bot) gets a real
#     read when a human adds the label. Any other label is a no-op (run=false).
# BUDGET — ONE whole-diff read per pull request. A later push is not re-read:
# the reviewer's findings live on review threads, and resolving an addressed
# thread is the session's own job, not another paid Opus pass per push. The
# review-findings gate holds the merge on those threads, so it clears on a
# resolution rather than on a re-review.
#
#   synchronize — a push. Reviews only when one of two conditions holds:
#       1. "[opus-review]" in the head commit TITLE — a full, on-demand Opus
#          re-read. Head-scoped (once-per-tag): the re-review fires for the
#          commit that carries the tag and NOT again on later untagged pushes
#          (re-tag to run again).
#       2. The reviewer left NO review of this pull request at all — re-arming
#          the read `opened` owed but never delivered, after a cancelled job
#          or an oversized diff. Self-terminating: the first review ends it.
#          Any review STATE spends the read, CHANGES_REQUESTED and COMMENTED
#          included — resolving an addressed finding is not this script's job.
#
# Read under pull_request_target, so the untrusted PR head is NEVER checked out
# or executed here: the head commit's message and the PR's reviews are fetched as
# DATA via the API and matched as FIXED strings (grep -F / exact compare, never
# eval). A transient API failure yields run=false (no review, no red) rather than
# a spurious re-review.
#
# Env: GH_TOKEN, ACTION, REPO, HEAD_SHA, PR, LABEL (LABEL set only on `labeled`);
# REVIEWER_LOGIN optional.
set -euo pipefail

KEYWORD="[opus-review]"
REVIEW_LABEL="needs-auto-review"
# The reviewer posts with GITHUB_TOKEN, so its reviews are authored by this bot;
# the latest review it left is the effective verdict that gates the PR.
# reviewer_login_init owns that identity for every reviewer script, including the
# REST/GraphQL `[bot]`-suffix mismatch (lib/reviewer-login.bash).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/reviewer-login.bash disable=SC1091
source "$SCRIPT_DIR/lib/reviewer-login.bash"
reviewer_login_init
# The one model behind every verdict that gates or clears a PR's merge — the
# first read and the re-check alike. Not a place to economize: a cheaper model
# that flips a hold to APPROVE clears the merge on its own judgement.
REVIEW_MODEL="claude-opus-5"

emit() {
  # $1 run, $2 reason
  local run="$1" reason="$2"
  {
    echo "run=$run"
    echo "model=$REVIEW_MODEL"
  } >>"$GITHUB_OUTPUT"
  echo "decision: run=$run model=$REVIEW_MODEL ($reason)"
}

case "$ACTION" in
opened | ready_for_review)
  emit true "first review on $ACTION"
  exit 0
  ;;
labeled)
  if [[ "${LABEL:-}" == "$REVIEW_LABEL" ]]; then
    emit true "on-demand review requested via '$REVIEW_LABEL' label"
  else
    emit false "labeled with '${LABEL:-}', not '$REVIEW_LABEL'"
  fi
  exit 0
  ;;
synchronize) ;;
*)
  emit false "no automatic review on '$ACTION'"
  exit 0
  ;;
esac

# synchronize, trigger 1: full Opus re-read on the [opus-review] opt-in in the
# head commit title. Fetch the head commit DIRECTLY by SHA — not the PR-commits
# list, which the API caps at 250 even with --paginate, so on a heavily-revised
# PR (exactly what this re-trigger serves) the head would fall off the list and
# the opt-in would silently fail. Capture into a variable (never `gh … | grep`,
# whose early-exit SIGPIPEs the still-writing gh under pipefail), then match the
# subject line.
# allow-exit-suppress: a transient API failure yields an empty message -> no
# keyword match below -> no spurious re-review trigger, the safe default.
message="$(gh api "repos/$REPO/commits/$HEAD_SHA" --jq '.commit.message' 2>/dev/null || true)"
subject="${message%%$'\n'*}"
if grep -qiF "$KEYWORD" <<<"$subject"; then
  emit true "$KEYWORD in head commit title"
  exit 0
fi

# synchronize, trigger 2: consumed only after trigger 1, so a tagged push pays
# no paginated GraphQL read it never uses. `--paginate --slurp` returns an array
# with ONE element PER PAGE (each element is that page's reviews array), so the
# filter must flatten BOTH levels (`.[][]`) to walk every review across every
# page, then `last` picks the most recent. A single `.[]` iterates PAGES, so
# `.user.login`/`.state` would index a page ARRAY — jq errors, and the re-arm
# silently never fires. The exit STATUS is captured separately from the state,
# because the two empty results mean opposite things: a successful "" is the
# strongest reason to review (nobody ever looked), while a failed "" must keep
# the fail-safe of not reviewing. Folded together they would review on every
# API blip.
reviews_rc=0
reviews_json="$(gh api "repos/$REPO/pulls/${PR:-}/reviews" --paginate --slurp 2>/dev/null)" || reviews_rc=$?
state="$(printf '%s' "$reviews_json" |
  jq -r "[.[][] | ${REVIEWER_MATCH_USER}] | last | .state // empty" 2>/dev/null || true)"
if [[ "$reviews_rc" -ne 0 ]]; then
  emit false "could not read $REPO#${PR:-} reviews (rc=$reviews_rc) — not reviewing rather than guessing"
elif [[ -z "$state" ]]; then
  emit true "$REVIEWER_LOGIN has never reviewed this PR — running the first pass on this $ACTION"
else
  emit false "$REVIEWER_LOGIN already reviewed this PR (latest: $state) — a $ACTION is not re-read; push a commit titled $KEYWORD for a full re-read"
fi
