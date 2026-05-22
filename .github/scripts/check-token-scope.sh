#!/usr/bin/env bash
# Check that the GitHub token used for template sync has the 'workflow' scope
# (only enforceable for classic PATs; fine-grained PATs and GITHUB_TOKEN don't
# expose scopes via the API response header).
#
# Inputs (env):
#   TOKEN  GitHub token to inspect

set -euo pipefail

: "${TOKEN:?TOKEN must be set}"

HEADERS=$(curl -sS -I -H "Authorization: token $TOKEN" \
  https://api.github.com/user 2>/dev/null || true)

if echo "$HEADERS" | grep -qi '^x-oauth-scopes:'; then
  SCOPES=$(echo "$HEADERS" | grep -i '^x-oauth-scopes:' | sed 's/^[^:]*: //' | tr -d '\r\n')
  if echo "$SCOPES" | tr ',' '\n' | sed 's/^ *//' | grep -qx 'workflow'; then
    echo "Classic PAT has 'workflow' scope."
  else
    echo "::error::Classic TEMPLATE_SYNC_TOKEN lacks the 'workflow' scope, which GitHub requires to push changes to .github/workflows/ files. Add the 'workflow' scope to your PAT at https://github.com/settings/tokens and update the TEMPLATE_SYNC_TOKEN repository secret."
    exit 1
  fi
else
  echo "Token does not expose OAuth scopes (fine-grained PAT or GITHUB_TOKEN); skipping scope check. Ensure 'Workflows: Read and write' is granted."
fi
