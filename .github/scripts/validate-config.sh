#!/usr/bin/env bash
set -euo pipefail

errors=0

error() {
  echo "ERROR: $1"
  errors=$((errors + 1))
}

echo "Validating configuration consistency..."
echo ""

# 1. All hook scripts referenced in .claude/settings.json exist on disk
echo "Checking Claude hook script paths..."
if [ -f .claude/settings.json ]; then
  commands=$(jq -r '.. | objects | select(.command?) | .command' .claude/settings.json 2>/dev/null || true)
  while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    # shellcheck disable=SC2016  # literal $CLAUDE_PROJECT_DIR matched by sed
    resolved=$(echo "$cmd" | sed 's|"\$CLAUDE_PROJECT_DIR"/\?|./|g; s|"||g; s|\$CLAUDE_PROJECT_DIR/\?|./|g')
    read -ra tokens <<<"$resolved"
    for token in "${tokens[@]}"; do
      case "$token" in
      ./.claude/hooks/* | ./.hooks/*)
        if [ ! -f "$token" ]; then
          error "Hook script missing: $token"
        fi
        ;;
      esac
    done
  done <<<"$commands"
else
  error ".claude/settings.json not found"
fi

# 2. Hook scripts are syntactically valid. Files with a shebang must be
# executable (they're invoked directly); language-helper files without a
# shebang are loaded by another hook and don't need +x.
echo "Checking hook script permissions and syntax..."
for f in .hooks/* .claude/hooks/*; do
  [ -f "$f" ] || continue
  has_shebang=0
  IFS= read -r first_line <"$f" || true
  case "$first_line" in '#!'*) has_shebang=1 ;; esac
  if [ "$has_shebang" = "1" ] && [ ! -x "$f" ]; then
    error "$f has a shebang but is not executable"
  fi
  case "$f" in
  *.py)
    if ! py_err=$(python3 -m py_compile "$f" 2>&1); then
      error "$f has a python syntax error: $py_err"
    fi
    ;;
  *)
    if ! bash_err=$(bash -n "$f" 2>&1); then
      error "$f has a bash syntax error: $bash_err"
    fi
    ;;
  esac
done

# Summary
echo ""
if [ "$errors" -gt 0 ]; then
  echo "Validation failed with $errors error(s)"
  exit 1
else
  echo "All checks passed"
fi
