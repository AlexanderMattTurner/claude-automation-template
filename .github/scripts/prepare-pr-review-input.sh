#!/usr/bin/env bash
# Fetch the untrusted PR diff + metadata and run them through the
# agent-input-sanitizer (sanitize-pr-input.mjs) BEFORE the review agent sees
# them. The agent reads only the sanitized files this writes — never the raw
# `gh pr diff` — so an injection payload hidden in the diff (zero-width control
# text, ANSI escapes, exfil beacons) cannot reach the agent intact.
#
# Oversized-diff guard: the base-only checkout means diff.txt is the ONLY source
# of the PR's changes — the agent cannot reconstruct them from the trusted base
# tree, so an enormous diff (a mega-merge, a vendored/generated dump) would be
# ingested whole into an Opus read that is slow, costly, and low-signal. Above
# MAX_DIFF_LINES lines this skips the review, emitting oversized=true so the
# caller posts a "please review manually" notice instead of spending the read.
#
# The guard has TWO sources, and it needs both. GitHub refuses to serve a diff
# over its own 20000-line cap with HTTP 406, so the line count below never sees
# the very PRs the guard was written for — the fetch fails first. That refusal IS
# an oversized verdict, and it is deterministic, so retrying it only spends the
# backoff ladder to learn the same answer and then reds the check the notice
# exists to replace. The line count still covers a caller whose MAX_DIFF_LINES
# sits below GitHub's cap.
#
# Requires: gh authenticated (GH_TOKEN/GH_REPO), node + `pnpm install` done
# (agent-input-sanitizer on the module path). Emits to GITHUB_OUTPUT:
#   oversized=true|false       — whether the review was skipped for size
#   diff_lines=<n>             — the diff's line count (only when oversized)
# Writes into $PR_INPUT_DIR (only when NOT oversized):
#   diff.txt / meta.txt        — sanitized diff and PR metadata
#   sanitizer-report.txt       — what was neutralized (never empty; says so)
# and, only when oversized:
#   oversized-notice.txt       — the human-review notice body for the caller
set -euo pipefail

# shellcheck source=.github/scripts/lib-ci-retry.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib-ci-retry.sh"

: "${PR:?PR number required}"
: "${PR_INPUT_DIR:?PR_INPUT_DIR required}"

MAX_DIFF_LINES="${MAX_DIFF_LINES:-20000}"

mkdir -p "$PR_INPUT_DIR"

emit_output() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s\n' "$1" >>"$GITHUB_OUTPUT"
  fi
}

# GitHub's own diff-API cap, and the phrase its refusal carries. Recorded from a
# real 406 on this repository: `could not find pull request diff: HTTP 406:
# Sorry, the diff exceeded the maximum number of lines (20000) (https://…)`.
API_DIFF_LINE_CAP=20000
API_OVERSIZE_MARKER="the diff exceeded the maximum number of lines"

# skip_as_oversized REASON LINES — the ONE place the size skip is decided, so
# both sources of the verdict emit the same outputs and the same notice. REASON
# completes "this PR's diff is …".
skip_as_oversized() {
  emit_output "oversized=true"
  emit_output "diff_lines=$2"
  printf '%s\n' \
    "Automated Opus review skipped: this PR's diff is $1. A change this large should get a human review — please review it manually." \
    >"${PR_INPUT_DIR}/oversized-notice.txt"
  echo "diff is $1; skipping review" >&2
  exit 0
}

# Materialize the raw diff OUTSIDE the agent-readable input dir (the review step
# grants the agent read over PR_INPUT_DIR via --add-dir), so only the SANITIZED
# diff.txt ever reaches the reviewer.
raw_diff="$(mktemp)"
fetch_err="$(mktemp)"
trap 'rm -f "$raw_diff" "$fetch_err"' EXIT

# --allow-escape-sequences is safe here: that byte reaches only the sanitizer
# below, never a real terminal.
fetch_diff() { gh pr diff "$PR" --allow-escape-sequences; }

# One unretried attempt first, so the size refusal is classified before the
# backoff ladder starts. Anything else is a blip and gets the full budget.
# retry_stdout via a command substitution: only the succeeding attempt's bytes
# land in raw_diff, where a plain `retry … >"$raw_diff"` would leak a failing
# attempt's error body into the file.
if raw_diff_content="$(fetch_diff 2>"$fetch_err")"; then
  :
elif grep -qF "$API_OVERSIZE_MARKER" "$fetch_err"; then
  skip_as_oversized \
    "over GitHub's own ${API_DIFF_LINE_CAP}-line diff API cap, so the API refused to serve it" \
    "$API_DIFF_LINE_CAP"
else
  cat "$fetch_err" >&2
  raw_diff_content="$(retry_stdout fetch_diff)"
fi
printf '%s\n' "$raw_diff_content" >"$raw_diff"

diff_lines="$(wc -l <"$raw_diff" | tr -d '[:space:]')"
if ((diff_lines > MAX_DIFF_LINES)); then
  skip_as_oversized \
    "${diff_lines} lines, over the ${MAX_DIFF_LINES}-line limit for automated review" \
    "$diff_lines"
fi
emit_output "oversized=false"

sanitize() { node .github/scripts/sanitize-pr-input.mjs; }

sanitize <"$raw_diff" >"${PR_INPUT_DIR}/diff.txt" 2>"${PR_INPUT_DIR}/diff.report.txt"
# Capture the metadata JSON with retry_stdout, THEN pipe the clean result into
# the sanitizer — retrying gh directly inside the `| sanitize` pipe is unsafe (a
# failing attempt would stream partial JSON into the sanitizer, and a SIGPIPE if
# it exited early would trip pipefail).
meta_json="$(retry_stdout gh pr view "$PR" --json title,body,author,files)"
printf '%s' "$meta_json" |
  sanitize >"${PR_INPUT_DIR}/meta.txt" 2>"${PR_INPUT_DIR}/meta.report.txt"

report="${PR_INPUT_DIR}/sanitizer-report.txt"
{
  if [[ -s "${PR_INPUT_DIR}/diff.report.txt" ]]; then
    echo "## Diff"
    cat "${PR_INPUT_DIR}/diff.report.txt"
  fi
  if [[ -s "${PR_INPUT_DIR}/meta.report.txt" ]]; then
    echo "## Metadata"
    cat "${PR_INPUT_DIR}/meta.report.txt"
  fi
} >"$report"

if [[ -s "$report" ]]; then
  echo "sanitizer neutralized injection-shaped content; see ${report}" >&2
else
  echo "(sanitizer found no injection-shaped content in the diff or metadata)" >"$report"
fi
