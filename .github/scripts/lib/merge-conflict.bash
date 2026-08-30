# shellcheck shell=bash
# kcov-exclude: library-only — sourced into the template-sync scripts, with no entry point of
#   its own, so there is nothing for kcov to invoke.
# Contract: sourced into strict-mode (set -euo pipefail) callers; do not re-set shell options.
#
# What the template-sync conflict path needs to read a merge. The auto-resolve resolver used to
# live in this tree and these two came from its shared library; it is now its own repository, so
# the copy the template still uses lives here.
#
# CONFLICT_MARKER_RE must stay ONE spelling across every reader: a second copy drifts, and a
# reader that finds no markers accepts a merge another reader refuses. It also matches
# `|||||||`, the diff3 base section. A marker verdict needs the COMPLETE triple — `=======`
# alone is legal Markdown and ordinary banner art, so one kind alone would call prose damage.

# shellcheck source=.github/scripts/lib/shared-names.bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/shared-names.bash"

# shellcheck disable=SC2034  # read by the scripts that source this file, never here
CONFLICT_MARKER_RE="$_CONFLICT_MARKER_RE"

# protected_matches PATH… — the subset touching security-sensitive trees. Override with
# AUTO_RESOLVE_PROTECTED_RE (an ERE). The default names only the trees every consumer has, so a
# repository that configures nothing still gets its automation and agent config flagged.
protected_matches() {
  local protected="${AUTO_RESOLVE_PROTECTED_RE:-^(\.github/|\.claude/|\.hooks/)}" f
  for f in "$@"; do
    [[ "$f" =~ $protected ]] && printf '%s\n' "$f"
  done
  return 0
}

# has_marker_triple — true when stdin carries all three marker kinds, each as a whole line.
# The complete triple is the verdict, never one kind on its own.
has_marker_triple() {
  local text kind
  text="$(cat)"
  for kind in '<' '=' '>'; do
    grep -qE "^${kind}{7}([ \t]|\$)" <<<"$text" || return 1
  done
}

# committed_marker_paths BASE_REF [REF] — paths whose content in REF (default HEAD) carries the
# marker triple. It reads the COMMIT and never the worktree, so an edit a resolver left unpushed
# cannot change the verdict about what a consumer would check out. A path whose BASE_REF copy
# already carries markers is skipped, so a repository that keeps marker text on purpose — a test
# fixture, a document about conflicts — is never withheld from itself.
committed_marker_paths() {
  local base_ref="${1:?committed_marker_paths: BASE_REF required}" ref="${2:-HEAD}" listing rc f
  listing="$(git grep -lE "$CONFLICT_MARKER_RE" "$ref" -- .)"
  rc=$?
  # 1 is "no match", the ordinary outcome on a branch nobody left a marker on.
  [[ "${rc:-0}" -le 1 ]] || return "$rc"
  while IFS= read -r f; do
    f="${f#"${ref}:"}"
    [[ -n "$f" ]] || continue
    git cat-file blob "${ref}:${f}" | has_marker_triple || continue
    git cat-file blob "${base_ref}:${f}" 2>/dev/null | has_marker_triple && continue
    printf '%s\n' "$f"
  done <<<"$listing"
  return 0
}
