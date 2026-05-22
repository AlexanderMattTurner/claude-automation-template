#!/usr/bin/env bash
# Sync template files into the current repo, producing outputs consumed by
# .github/workflows/template-sync.yaml.
#
# Inputs (env):
#   SYNC_PATHS        Space-separated paths to sync from the template
#   EXCLUDE_PATHS     Space-separated paths to exclude (subset of SYNC_PATHS)
#   GITHUB_OUTPUT     Path to GitHub Actions output file (optional outside CI)
#
# Assumes a sibling `_template/` directory containing a checkout of the
# template repository at the desired ref. Reads `.template-version` (if
# present) for the previously synced SHA and overwrites it with the new one.
#
# Side effects:
#   - Creates/updates files inside the current repo to match the template
#   - Writes /tmp/conflict_files.txt, /tmp/conflict_report.md,
#     /tmp/deleted_files.txt, /tmp/auto_merged_files.txt
#   - Writes .template-sync-conflicts if there are unresolved conflicts
#   - Appends key=value lines to $GITHUB_OUTPUT when set

set -euo pipefail

SYNC_PATHS="${SYNC_PATHS:-}"
EXCLUDE_PATHS="${EXCLUDE_PATHS:-}"
GITHUB_OUTPUT="${GITHUB_OUTPUT:-/dev/null}"

# Allow tests to point at alternative temp dirs.
WORK_DIR="${TEMPLATE_SYNC_WORK_DIR:-/tmp}"
CONFLICT_FILES="$WORK_DIR/conflict_files.txt"
CONFLICT_REPORT="$WORK_DIR/conflict_report.md"
DELETED_FILES="$WORK_DIR/deleted_files.txt"
AUTO_MERGED_FILES="$WORK_DIR/auto_merged_files.txt"
PREV_TEMPLATE_FILES="$WORK_DIR/prev_template_files.txt"

: >"$CONFLICT_FILES"
: >"$CONFLICT_REPORT"
: >"$DELETED_FILES"
: >"$AUTO_MERGED_FILES"

#############################################
# Version tracking
#############################################

TEMPLATE_SHA=$(git -C _template rev-parse HEAD)
TEMPLATE_SHA_SHORT="${TEMPLATE_SHA:0:7}"
{
  echo "template_sha=$TEMPLATE_SHA"
  echo "template_sha_short=$TEMPLATE_SHA_SHORT"
} >>"$GITHUB_OUTPUT"

PREV_SHA=""
if [ -f .template-version ]; then
  PREV_SHA=$(cat .template-version)
  echo "Previous template version: $PREV_SHA"
else
  echo "No previous template version found (first sync)"
fi
echo "Current template version: $TEMPLATE_SHA"

if [ -n "$PREV_SHA" ] && [ "$PREV_SHA" != "$TEMPLATE_SHA" ]; then
  if git -C _template cat-file -e "$PREV_SHA" 2>/dev/null; then
    CHANGELOG=$(git -C _template log --oneline "$PREV_SHA..$TEMPLATE_SHA")
  else
    echo "::warning::Previous template SHA $PREV_SHA not found in template history (likely rewritten by force-push or rebase)"
    CHANGELOG="Previous SHA \`$PREV_SHA\` no longer exists in template history (force-push/rebase). Showing last 20 commits instead:"$'\n'
    CHANGELOG+=$(git -C _template log --oneline -20 "$TEMPLATE_SHA")
  fi
  if [ -n "$CHANGELOG" ]; then
    {
      echo "changelog<<CHANGELOG_DELIMITER_8a2b1c"
      echo "$CHANGELOG"
      echo "CHANGELOG_DELIMITER_8a2b1c"
    } >>"$GITHUB_OUTPUT"
  fi
fi

echo "$TEMPLATE_SHA" >.template-version

#############################################
# File processing
#############################################

# 3-way merge a single file, or apply the template version when no base exists.
process_file() {
  local rel_path="$1"
  local template_file="_template/$rel_path"

  local parent_dir
  parent_dir=$(dirname "$rel_path")
  [ "$parent_dir" != "." ] && mkdir -p "$parent_dir"

  if [ ! -f "$rel_path" ]; then
    cp "$template_file" "$rel_path"
    echo "Added: $rel_path"
    return
  fi

  if diff -q "$rel_path" "$template_file" >/dev/null 2>&1; then
    return
  fi

  if [ -n "$PREV_SHA" ]; then
    local safe_name
    safe_name=$(echo "$rel_path" | tr '/' '_')
    local base_file="$WORK_DIR/merge_base_${safe_name}"

    if git -C _template show "${PREV_SHA}:${rel_path}" >"$base_file" 2>/dev/null; then
      if diff -q "$base_file" "$template_file" >/dev/null 2>&1; then
        echo "Unchanged in template: $rel_path (keeping local version)"
        rm -f "$base_file"
        return
      fi

      if diff -q "$base_file" "$rel_path" >/dev/null 2>&1; then
        cp "$template_file" "$rel_path"
        echo "Updated: $rel_path (local was unmodified)"
        rm -f "$base_file"
        return
      fi

      local merge_result="$WORK_DIR/merge_result_${safe_name}"
      cp "$rel_path" "$merge_result"

      if git merge-file -L "local" -L "base" -L "template" \
        "$merge_result" "$base_file" "$template_file" 2>/dev/null; then
        cp "$merge_result" "$rel_path"
        echo "Auto-merged: $rel_path (clean 3-way merge)"
        echo "$rel_path" >>"$AUTO_MERGED_FILES"
        rm -f "$base_file" "$merge_result"
        return
      else
        cp "$merge_result" "$rel_path"
        echo "CONFLICT (merge markers): $rel_path"
        echo "$rel_path" >>"$CONFLICT_FILES"
        {
          echo "### \`$rel_path\`"
          echo ""
          echo "3-way merge produced **conflict markers** (\`<<<<<<<\`/\`=======\`/\`>>>>>>>\`)."
          echo "Resolve them: keep local customizations, adopt template improvements."
          echo ""
          echo "<details>"
          echo "<summary>View file with conflict markers</summary>"
          echo ""
          echo "\`\`\`"
          head -200 "$rel_path"
          echo "\`\`\`"
          echo "</details>"
          echo ""
        } >>"$CONFLICT_REPORT"
        rm -f "$base_file" "$merge_result"
        return
      fi
    fi
    rm -f "$base_file"
  fi

  echo "CONFLICT (no base): $rel_path"
  echo "$rel_path" >>"$CONFLICT_FILES"
  {
    echo "### \`$rel_path\`"
    echo ""
    echo "No merge base available (first sync or file history unavailable)."
    echo "Template version has been applied. Restore any important local customizations."
    echo ""
    echo "<details>"
    echo "<summary>Diff (old local → new template)</summary>"
    echo ""
    echo "\`\`\`diff"
    diff -u "$rel_path" "$template_file" | head -200 || true
    echo "\`\`\`"
    echo "</details>"
    echo ""
  } >>"$CONFLICT_REPORT"

  cp "$template_file" "$rel_path"
}

#############################################
# Detect deleted files
#############################################

# A path is "deleted" only if it existed in the template at PREV_SHA but no
# longer exists at the current template HEAD. This avoids false positives for
# project-specific files that were never in the template.
if [ -n "$PREV_SHA" ]; then
  git -C _template ls-tree -r --name-only "$PREV_SHA" 2>/dev/null >"$PREV_TEMPLATE_FILES" || true

  for path in $SYNC_PATHS; do
    skip=false
    for exclude in $EXCLUDE_PATHS; do
      [ "$path" = "$exclude" ] && skip=true && break
    done
    [ "$skip" = "true" ] && continue

    while IFS= read -r prev_file; do
      case "$prev_file" in "$path" | "$path/"*) ;; *) continue ;; esac

      if [ ! -f "_template/$prev_file" ]; then
        echo "DELETED in template: $prev_file"
        echo "$prev_file" >>"$DELETED_FILES"
      fi
    done <"$PREV_TEMPLATE_FILES"
  done
fi

#############################################
# Process sync paths
#############################################

for path in $SYNC_PATHS; do
  skip=false
  for exclude in $EXCLUDE_PATHS; do
    if [ "$path" = "$exclude" ]; then
      skip=true
      break
    fi
  done
  [ "$skip" = "true" ] && continue

  if [ ! -e "_template/$path" ]; then
    echo "Warning: $path not found in template, skipping"
    continue
  fi

  if [ -d "_template/$path" ]; then
    while IFS= read -r template_file; do
      rel_path="${template_file#_template/}"
      process_file "$rel_path"
    done < <(find "_template/$path" -type f)
  else
    process_file "$path"
  fi
done

rm -rf _template

#############################################
# Set outputs
#############################################

if [ -s "$AUTO_MERGED_FILES" ]; then
  auto_merged=$(tr '\n' ' ' <"$AUTO_MERGED_FILES")
  echo "auto_merged_files=$auto_merged" >>"$GITHUB_OUTPUT"
fi

if [ -s "$CONFLICT_FILES" ]; then
  conflicts=$(tr '\n' ' ' <"$CONFLICT_FILES")
  {
    echo "has_conflicts=true"
    echo "conflict_files=$conflicts"
    echo "conflict_report<<CONFLICT_REPORT_DELIMITER_7f3d9a"
    cat "$CONFLICT_REPORT"
    echo "CONFLICT_REPORT_DELIMITER_7f3d9a"
  } >>"$GITHUB_OUTPUT"

  echo "Template updates available for: $conflicts" >.template-sync-conflicts
else
  echo "has_conflicts=false" >>"$GITHUB_OUTPUT"
  rm -f .template-sync-conflicts
fi

if [ -s "$DELETED_FILES" ]; then
  deleted=$(tr '\n' ' ' <"$DELETED_FILES")
  {
    echo "has_deletions=true"
    echo "deleted_files=$deleted"
  } >>"$GITHUB_OUTPUT"
else
  echo "has_deletions=false" >>"$GITHUB_OUTPUT"
fi

if git diff --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  echo "has_changes=false" >>"$GITHUB_OUTPUT"
else
  changed_paths=$({
    git diff --name-only
    git ls-files --others --exclude-standard
  } | tr '\n' ' ')
  {
    echo "has_changes=true"
    echo "changed_paths=$changed_paths"
  } >>"$GITHUB_OUTPUT"
fi
