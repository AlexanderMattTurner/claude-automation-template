"""The `gh` stub that drives `label-merge-conflicts.sh` in tests.

One stub for one script: a second near-identical copy drifts, and the copy that
drops a route silently stops covering the code path that route belongs to.

The script's every `gh` call is answered from the environment: `PR_ROWS` is a
JSON file of pull-request rows, `COMPARE_STATUS` the answer to a compare, and
`CALL_LOG` collects the full argv so a test can pin which URL was asked for. An
empty `COMPARE_STATUS` is a non-zero exit — an API fault, not an empty read.
"""

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$CALL_LOG"
case "$1 $2" in
  "label create") exit 0 ;;
  "pr list") cat "$PR_ROWS" ;;
  # Real `gh pr view` emits ONE PR object, then applies its own --jq (the
  # script's last argument) to it — so a script that dropped --jq would get an
  # object where list_prs() iterates an array.
  "pr view")
    obj="$(jq -c '.[0]' "$PR_ROWS")"
    if [[ "$*" == *--jq* ]]; then jq -c "${@: -1}" <<<"$obj"; else echo "$obj"; fi
    ;;
  "pr edit") exit 0 ;;
  "api repos/o/r/compare/"*) [[ -n "$COMPARE_STATUS" ]] || exit 1; echo "$COMPARE_STATUS" ;;
  *) echo "fake gh: unhandled: $*" >&2; exit 1 ;;
esac
"""
