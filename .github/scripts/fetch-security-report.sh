#!/usr/bin/env bash
# Collect open security alerts (Dependabot, code scanning, secret scanning,
# pnpm audit, Socket.dev) into a single Markdown report. Writes the report to
# $REPORT_PATH and exports SECURITY_REPORT (first 50KB) to $GITHUB_ENV.
#
# Inputs (env):
#   GH_TOKEN       GitHub token (Dependabot/secret APIs require security_events scope)
#   REPO           owner/repo
#   GITHUB_ENV     Path to GitHub Actions env file (optional outside CI)
#   REPORT_PATH    Output report file (default: /tmp/security-report.md)

# --jq arguments are literal jq expressions; $-tokens in jq strings (e.g.
# `\(.number)`) are intentional and shouldn't be shell-expanded.
# shellcheck disable=SC2016

set -uo pipefail

: "${GH_TOKEN:?GH_TOKEN must be set}"
: "${REPO:?REPO must be set (owner/repo)}"
GITHUB_ENV="${GITHUB_ENV:-/dev/null}"
REPORT_PATH="${REPORT_PATH:-/tmp/security-report.md}"

echo "## Dependabot Alerts" >"$REPORT_PATH"
gh api "repos/${REPO}/dependabot/alerts?state=open&per_page=100" \
  --jq '.[] | "- **\(.security_advisory.severity | ascii_upcase)**: [\(.security_advisory.summary)](https://github.com/'"${REPO}"'/security/dependabot/\(.number)) in `\(.dependency.package.name)` (\(.dependency.package.ecosystem))"' \
  >>"$REPORT_PATH" 2>&1 || echo "_Could not fetch Dependabot alerts (check repo permissions)._" >>"$REPORT_PATH"

{
  echo ""
  echo "## Code Scanning Alerts"
} >>"$REPORT_PATH"
gh api "repos/${REPO}/code-scanning/alerts?state=open&per_page=100" \
  --jq '.[] | "- **\(.rule.severity // .rule.security_severity_level | ascii_upcase)**: [\(.rule.description)](https://github.com/'"${REPO}"'/security/code-scanning/\(.number)) at `\(.most_recent_instance.location.path):\(.most_recent_instance.location.start_line)`"' \
  >>"$REPORT_PATH" 2>&1 || echo "_No code scanning alerts or code scanning not enabled._" >>"$REPORT_PATH"

{
  echo ""
  echo "## Secret Scanning Alerts"
} >>"$REPORT_PATH"
gh api "repos/${REPO}/secret-scanning/alerts?state=open&per_page=100" \
  --jq '.[] | "- **\(.state | ascii_upcase)**: \(.secret_type_display_name) — [Alert #\(.number)](https://github.com/'"${REPO}"'/security/secret-scanning/\(.number))"' \
  >>"$REPORT_PATH" 2>&1 || echo "_No secret scanning alerts or secret scanning not enabled._" >>"$REPORT_PATH"

{
  echo ""
  echo "## pnpm audit"
} >>"$REPORT_PATH"
pnpm audit 2>&1 | head -100 >>"$REPORT_PATH" || true

{
  echo ""
  echo "## Socket.dev Alerts"
} >>"$REPORT_PATH"

# Bot username is "socket-security[bot]" (as of 2025); if Socket changes
# their bot name this will silently return no results.
SOCKET_FOUND=false
for pr_num in $(gh api "repos/${REPO}/pulls?state=open&per_page=5" --jq '.[].number' 2>/dev/null); do
  SOCKET_COMMENTS=$(gh api "repos/${REPO}/issues/${pr_num}/comments?per_page=30" \
    --jq '[.[] | select(.user.login == "socket-security[bot]")] | length' \
    2>/dev/null || echo "0")
  if [ "$SOCKET_COMMENTS" != "0" ]; then
    SOCKET_FOUND=true
    echo "### PR #${pr_num}" >>"$REPORT_PATH"
    gh api "repos/${REPO}/issues/${pr_num}/comments?per_page=30" \
      --jq '.[] | select(.user.login == "socket-security[bot]") | .body' \
      2>/dev/null >>"$REPORT_PATH" || true
    echo "" >>"$REPORT_PATH"
  fi
done
if [ "$SOCKET_FOUND" = "false" ]; then
  echo "_No Socket.dev alerts found in recent open PRs._" >>"$REPORT_PATH"
fi

cat "$REPORT_PATH"

{
  echo "SECURITY_REPORT<<REPORT_EOF"
  head -c 50000 "$REPORT_PATH"
  echo ""
  echo "REPORT_EOF"
} >>"$GITHUB_ENV"
