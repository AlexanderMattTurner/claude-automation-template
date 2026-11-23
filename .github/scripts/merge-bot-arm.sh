#!/usr/bin/env bash
# Arm a pull request's merge as the merge-bot GitHub App, so the App authors the
# merge commit instead of a person.
#
# PROBLEM CLASS — GitHub stamps a merge commit with the "last known timezone" of
# whoever armed the merge, which publishes a person's UTC offset in the history.
# An App has no timezone, so its merges carry +0000. An App's merge also starts
# the push workflows, which a merge armed with GITHUB_TOKEN does not.
#
# Two events reach this script (see merge-bot.yaml):
#   auto_merge_enabled  a person armed auto-merge; take the arming over.
#   labeled             a person added the `merge-bot` label; arm from scratch,
#                       or take over an arming that is already there.
#
# Env: GH_TOKEN (the App's token), GH_REPO, PR_NUMBER, BOT_LOGIN (the App's REST
# login, `<slug>[bot]`), EVENT_ACTION, SENDER (who fired the event).
set -euo pipefail

: "${GH_REPO:?GH_REPO required}"
: "${PR_NUMBER:?PR_NUMBER required}"
: "${BOT_LOGIN:?BOT_LOGIN required}"
: "${EVENT_ACTION:?EVENT_ACTION required}"

read_pr() { gh api "repos/${GH_REPO}/pulls/${PR_NUMBER}"; }

pr="$(read_pr)"
if [[ "$(jq -r '.state' <<<"$pr")" != "open" ]]; then
  echo "PR #${PR_NUMBER} is not open; nothing to arm."
  exit 0
fi

armed_by="$(jq -r '.auto_merge.enabled_by.login // ""' <<<"$pr")"
if [[ "$armed_by" == "$BOT_LOGIN" ]]; then
  echo "PR #${PR_NUMBER} is already armed by ${BOT_LOGIN}."
  exit 0
fi

merge_args=(--auto)
if [[ -n "$armed_by" ]]; then
  # Someone who may merge armed this PR, so either event may hand it to the App
  # with their choices kept. REST spells the method as the flag does.
  merge_args+=("--$(jq -r '.auto_merge.merge_method' <<<"$pr")")
  title="$(jq -r '.auto_merge.commit_title // ""' <<<"$pr")"
  message="$(jq -r '.auto_merge.commit_message // ""' <<<"$pr")"
  [[ -z "$title" ]] || merge_args+=(--subject "$title")
  [[ -z "$message" ]] || merge_args+=(--body "$message")
  gh pr merge "$PR_NUMBER" --disable-auto
elif [[ "$EVENT_ACTION" == "labeled" ]]; then
  # pull_request_target lets a triage-role user add a label, and that role may
  # not merge. Only a sender who could merge by hand may arm from scratch.
  : "${SENDER:?SENDER required}"
  permission="$(gh api "repos/${GH_REPO}/collaborators/${SENDER}/permission" | jq -r '.permission')"
  if [[ "$permission" != "admin" && "$permission" != "write" ]]; then
    echo "::error::${SENDER} has '${permission}' access to ${GH_REPO}, which cannot merge; not arming PR #${PR_NUMBER}."
    exit 1
  fi
  merge_args+=("--$(gh api "repos/${GH_REPO}" | jq -r '
    if .allow_merge_commit then "merge" elif .allow_squash_merge then "squash" else "rebase" end')")
else
  echo "Auto-merge on PR #${PR_NUMBER} is off, so there is no arming to take over."
  exit 0
fi

# The read-back below is the verdict, so a refusal here is reported, not fatal.
if ! gh pr merge "$PR_NUMBER" "${merge_args[@]}"; then
  echo "::warning::gh pr merge exited non-zero on PR #${PR_NUMBER}; reading the PR back." >&2
fi

pr="$(read_pr)"
if [[ "$(jq -r '.merged' <<<"$pr")" == "true" ]]; then
  echo "PR #${PR_NUMBER} merged."
  exit 0
fi
if [[ "$(jq -r '.auto_merge.enabled_by.login // ""' <<<"$pr")" == "$BOT_LOGIN" ]]; then
  echo "PR #${PR_NUMBER} is armed by ${BOT_LOGIN}. The required checks still gate the merge."
  exit 0
fi
now_off=""
[[ -z "$armed_by" ]] || now_off=" ${armed_by} had armed its auto-merge, which is now OFF: arm it again by hand."
echo "::error::PR #${PR_NUMBER} is not armed by ${BOT_LOGIN}.${now_off} Check that the App is installed on ${GH_REPO} with contents, pull requests and workflows write access."
exit 1
