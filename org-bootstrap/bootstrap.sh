#!/usr/bin/env bash
# Provision org-level secrets, a branch-protection ruleset, and default repo
# settings across the template-based repos in a GitHub organization. Idempotent:
# re-running converges to the configured state instead of duplicating anything.
#
# Usage:
#   ./bootstrap.sh secrets    # push org Actions secrets (values from env)
#   ./bootstrap.sh ruleset    # create/update the org branch-protection ruleset
#   ./bootstrap.sh defaults   # apply org + per-repo default settings
#   ./bootstrap.sh all        # all of the above, in order
#
# Requires: gh (authenticated, admin:org + repo scopes) and jq. See README.md.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config="${here}/config.sh"

if [[ ! -f "$config" ]]; then
  echo "ERROR: ${config} not found. Copy config.example.sh to config.sh and edit it." >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$config"

for tool in gh jq; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: ${tool} is required but not installed." >&2
    exit 1
  fi
done

if [[ -z "${ORG:-}" || "$ORG" == "your-org" ]]; then
  echo "ERROR: set ORG in config.sh." >&2
  exit 1
fi

# Echo every gh write so a run is auditable.
log() { printf '  %s\n' "$*"; }

# Names of the repos to manage: every non-archived repo, optionally narrowed to
# those carrying MANAGED_TOPIC. Emitted one per line for `while read`.
managed_repos() {
  local args=(--no-archived --limit 1000 --json name --jq '.[].name')
  if [[ -n "${MANAGED_TOPIC:-}" ]]; then
    args=(--topic "$MANAGED_TOPIC" "${args[@]}")
  fi
  gh repo list "$ORG" "${args[@]}"
}

sync_secrets() {
  echo "Syncing org secrets to ${ORG} (visibility=${SECRET_VISIBILITY})..."
  if [[ "${#SECRET_NAMES[@]}" -eq 0 ]]; then
    log "no secrets configured"
    return
  fi
  local name value
  for name in "${SECRET_NAMES[@]}"; do
    value="${!name:-}"
    if [[ -z "$value" ]]; then
      log "skip ${name}: no value in environment"
      continue
    fi
    printf '%s' "$value" |
      gh secret set "$name" --org "$ORG" --visibility "$SECRET_VISIBILITY"
    log "set ${name}"
  done
}

# Build the required_status_checks rule array from REQUIRED_CHECKS.
checks_payload() {
  if [[ "${#REQUIRED_CHECKS[@]}" -eq 0 ]]; then
    echo "[]"
    return
  fi
  printf '%s\n' "${REQUIRED_CHECKS[@]}" | jq -R '{context: .}' | jq -s '.'
}

sync_ruleset() {
  echo "Syncing branch-protection ruleset '${RULESET_NAME}' on ${ORG}..."
  local checks rules payload existing_id
  checks="$(checks_payload)"

  rules="$(jq -n --argjson checks "$checks" '
    [
      { "type": "deletion" },
      { "type": "non_fast_forward" },
      { "type": "required_status_checks",
        "parameters": {
          "strict_required_status_checks_policy": true,
          "required_status_checks": $checks
        }
      }
    ]')"

  if [[ "${REQUIRE_PULL_REQUEST:-false}" == "true" ]]; then
    rules="$(jq --argjson n "${REQUIRED_APPROVALS:-0}" '. + [
      { "type": "pull_request",
        "parameters": {
          "required_approving_review_count": $n,
          "dismiss_stale_reviews_on_push": true,
          "require_code_owner_review": false,
          "require_last_push_approval": false,
          "required_review_thread_resolution": false
        }
      }]' <<<"$rules")"
  fi

  payload="$(jq -n --arg name "$RULESET_NAME" --argjson rules "$rules" '
    {
      "name": $name,
      "target": "branch",
      "enforcement": "active",
      "conditions": {
        "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] },
        "repository_name": { "include": ["~ALL"], "exclude": [] }
      },
      "rules": $rules
    }')"

  existing_id="$(gh api "orgs/${ORG}/rulesets" |
    jq -r --arg n "$RULESET_NAME" 'map(select(.name == $n))[0].id // empty')"

  if [[ -n "$existing_id" ]]; then
    gh api -X PUT "orgs/${ORG}/rulesets/${existing_id}" --input - <<<"$payload" >/dev/null
    log "updated ruleset ${existing_id}"
  else
    gh api -X POST "orgs/${ORG}/rulesets" --input - <<<"$payload" >/dev/null
    log "created ruleset '${RULESET_NAME}'"
  fi
}

apply_defaults() {
  echo "Applying org defaults to ${ORG}..."
  gh api -X PATCH "orgs/${ORG}" \
    -f "default_repository_permission=${DEFAULT_REPO_PERMISSION}" \
    -F "members_can_create_repositories=${MEMBERS_CAN_CREATE_REPOS}" >/dev/null
  gh api -X PUT "orgs/${ORG}/actions/permissions/workflow" \
    -f "default_workflow_permissions=${DEFAULT_WORKFLOW_PERMISSIONS}" \
    -F "can_approve_pull_request_reviews=false" >/dev/null
  log "org settings applied"

  echo "Applying per-repo merge settings to managed repos..."
  local repo
  while IFS= read -r repo; do
    if [[ -z "$repo" ]]; then
      continue
    fi
    gh api -X PATCH "repos/${ORG}/${repo}" \
      -F "allow_squash_merge=${ALLOW_SQUASH_MERGE}" \
      -F "allow_merge_commit=${ALLOW_MERGE_COMMIT}" \
      -F "allow_rebase_merge=${ALLOW_REBASE_MERGE}" \
      -F "delete_branch_on_merge=${DELETE_BRANCH_ON_MERGE}" \
      -F "allow_auto_merge=${ALLOW_AUTO_MERGE}" >/dev/null
    log "settings applied: ${repo}"
  done < <(managed_repos)
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
  secrets) sync_secrets ;;
  ruleset) sync_ruleset ;;
  defaults) apply_defaults ;;
  all)
    sync_secrets
    sync_ruleset
    apply_defaults
    ;;
  *)
    echo "Usage: $0 {secrets|ruleset|defaults|all}" >&2
    exit 1
    ;;
  esac
  echo "Done."
}

main "$@"
