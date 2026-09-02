#!/usr/bin/env bash
# Keep the `merge-conflict` label on every open PR whose GitHub-computed
# mergeability is CONFLICTING, and clear it once the PR merges cleanly again.
# Conflict cost scales with how long a branch sits behind a fast-moving base,
# so surfacing the transition the moment it happens (instead of at merge time,
# hundreds of commits later) is what keeps resolutions small enough to review
# honestly. Event-driven with a cron backstop; API-only — it never pushes to a
# PR branch and never triggers a CI run on one.
#
# Scope: with PR_NUMBER set (a PR event) it syncs that one PR; unset (a base
# push / schedule) it scans every open PR. A single-PR sync is what clears the
# label seconds after a conflict is resolved.
#
# GitHub computes mergeability lazily: querying a PR triggers the computation,
# so a PR reporting UNKNOWN on the first pass usually resolves by a later one.
# PRs still UNKNOWN after MAX_PASSES are named in a workflow warning — never
# silently skipped — and the next event or scheduled run retries them anyway.
# GitHub also serves a CONFLICTING verdict that cannot be true: a head that
# already carries its base branch's tip merges as a fast-forward, so nothing can
# conflict. That happens on a stacked chain whose parent was merged into the
# child, and GitHub keeps serving it, so every scan re-labels the same PR.
# head_contains_base below reads such a verdict as MERGEABLE.
#
# Env: GH_TOKEN, REPO; PR_NUMBER scopes to one PR; MAX_PASSES (default 2) caps
# the retry loop; RETRY_DELAY_SECS overrides the between-pass wait; SWEEP_LIMIT
# (default 100) caps how many open PRs one full-repo sweep lists.
set -euo pipefail

: "${GH_TOKEN:?}" "${REPO:?}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=.github/scripts/lib-ci-retry.sh
source "$SCRIPT_DIR/lib-ci-retry.sh"

export LABEL="merge-conflict"

# retry on every gh call: a transient GitHub API 5xx must not red the labeler,
# and must not answer a question whose wrong answer edits a label.
retry gh label create "$LABEL" --repo "$REPO" --color d93f0b --force \
  --description "This PR has merge conflicts with its base branch"

SWEEP_LIMIT="${SWEEP_LIMIT:-100}"
# Set once the cap warning has fired, so a multi-pass retry (MAX_PASSES) that
# keeps re-fetching the same capped page reports it once, not once per pass.
# Must be set from the main loop, never from inside a command substitution —
# a subshell's assignment never reaches back to this variable's parent shell.
sweep_capped_warned=""

# Raw JSON: a single PR wrapped in an array (`pr view`), or an open-PR page
# (`pr list`) — a uniform shape so the caller never special-cases PR_NUMBER.
fetch_page() {
  if [[ -n "${PR_NUMBER:-}" ]]; then
    retry_stdout gh pr view "$PR_NUMBER" --repo "$REPO" \
      --json number,mergeable,labels,headRefOid,baseRefName --jq '[.]'
    return
  fi
  retry_stdout gh pr list --repo "$REPO" --state open --limit "$SWEEP_LIMIT" \
    --json number,mergeable,labels,headRefOid,baseRefName
}

# One row per PR from a fetch_page JSON blob: number, mergeable, whether LABEL
# is already applied, the head commit, the base branch name. Fields join with
# \x1f (ASCII unit separator), not @tsv's tab: bash always treats tab as IFS
# whitespace, so `read` squashes a run of tabs into one delimiter and an empty
# middle field would shift every field after it one to the left. \x1f is not IFS
# whitespace, so an empty field still costs exactly one delimiter.
list_prs() {
  local jq_row='[.number, .mergeable, any(.labels[]; .name == env.LABEL),
    (.headRefOid // ""), (.baseRefName // "")] | map(tostring) | join("\u001f")'
  jq -r ".[] | $jq_row" <<<"$1"
}

declare -A base_tip_cache=()
# Set the variable `_bt_target` names to the tip `_bt_ref` carries on the remote
# right now, memoized for one pass. Empty when the branch is gone or the read
# fails; the caller then leaves GitHub's verdict standing, since it has no tip to
# judge it by. Assigning by name is what makes the memo work: a `$(base_tip ...)`
# capture runs the call in a subshell, so the cache write never reaches this
# shell and every row spends another read of the same ref.
base_tip() {
  local _bt_target="$1" _bt_ref="$2" _bt_tip=""
  if [[ -z "${base_tip_cache[$_bt_ref]+set}" ]]; then
    # A failed read reports through this same output, so drop it rather than
    # cache an error message as a sha.
    if ! _bt_tip="$(retry_stdout gh api "repos/$REPO/git/ref/heads/$_bt_ref" --jq .object.sha)"; then
      _bt_tip=""
    fi
    base_tip_cache["$_bt_ref"]="$_bt_tip"
  fi
  printf -v "$_bt_target" '%s' "${base_tip_cache[$_bt_ref]}"
}

# Whether `head_oid` already carries the tip `base_ref` has right now. Merging
# such a head into its base is a fast-forward, so no conflict is possible and a
# CONFLICTING verdict about it is wrong.
#
# An unreadable tip or compare answers "not contained", which leaves GitHub's
# verdict standing. Both reads are retried first, so a transient API fault does
# not label a contained head.
head_contains_base() {
  local head_oid="$1" base_ref="$2" tip="" status
  [[ -n "$base_ref" ]] || return 1
  base_tip tip "$base_ref"
  [[ -n "$head_oid" && -n "$tip" ]] || return 1
  status="$(retry_stdout gh api "repos/$REPO/compare/${tip}...${head_oid}" --jq .status)" ||
    return 1
  [[ "$status" == "ahead" || "$status" == "identical" ]]
}

unknown=""
# retry-loop-ok: not a retry-until-success loop — each pass labels every PR
# whose state IS known this pass and only carries the still-UNKNOWN subset
# forward, so the repo's single-command retry_cmd has no body to wrap here.
for ((pass = 1; pass <= ${MAX_PASSES:-2}; pass++)); do
  [[ "$pass" == "1" ]] || sleep "${RETRY_DELAY_SECS:-10}"
  # One pass is one snapshot of the remote: a memo kept across passes would
  # judge a later pass against a tip the base branch has already moved off.
  base_tip_cache=()
  unknown=""
  page="$(fetch_page)"
  # A full page means more open PRs may exist past the limit; say so rather
  # than silently under-sweeping them. jq's own array length, not a line count
  # of the rendered rows — a zero-PR page renders as one blank TSV line.
  if [[ -z "${PR_NUMBER:-}" && -z "$sweep_capped_warned" &&
    "$(jq 'length' <<<"$page")" -ge "$SWEEP_LIMIT" ]]; then
    echo "::warning::open-PR sweep hit its $SWEEP_LIMIT-PR limit; some PRs may not have been checked this run." >&2
    sweep_capped_warned=1
  fi
  while IFS=$'\x1f' read -r num state labeled head_oid base_ref; do
    [[ -n "$num" ]] || continue
    if [[ "$state" == "CONFLICTING" ]] && head_contains_base "$head_oid" "$base_ref"; then
      echo "::notice::#$num's head already contains $base_ref's tip, so the merge is a fast-forward; reading GitHub's CONFLICTING verdict as wrong."
      state="MERGEABLE"
    fi
    case "$state" in
    CONFLICTING)
      [[ "$labeled" == "true" ]] || retry gh pr edit "$num" --repo "$REPO" --add-label "$LABEL"
      ;;
    MERGEABLE)
      [[ "$labeled" == "false" ]] || retry gh pr edit "$num" --repo "$REPO" --remove-label "$LABEL"
      ;;
    *)
      unknown="$unknown #$num"
      ;;
    esac
  done <<<"$(list_prs "$page")"
  [[ -n "$unknown" ]] || break
done

if [[ -n "$unknown" ]]; then
  echo "::warning::mergeability still UNKNOWN for$unknown after ${MAX_PASSES:-2} passes; the next PR event or scheduled run will retry them."
fi
