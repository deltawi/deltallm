#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-http://localhost:4000}"
MASTER_KEY="${MASTER_KEY:-}"
ORG_ID="${ORG_ID:-org-e2e-governance}"
TEAM_ID="${TEAM_ID:-team-e2e-governance}"
USER_ID="${USER_ID:-user-e2e-governance}"
MODEL_KEY="${MODEL_KEY:-support-chat}"
SECONDARY_MODEL_KEY="${SECONDARY_MODEL_KEY:-llama-3.1-8b-instant}"

if [[ -z "$MASTER_KEY" ]]; then
  echo "MASTER_KEY is required"
  exit 1
fi

auth_header=(-H "Authorization: Bearer ${MASTER_KEY}")
json_header=(-H "Content-Type: application/json")

echo "== Create organization with initial callable-target access =="
curl -sS -X POST "${BASE}/ui/api/organizations" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"organization_id\": \"${ORG_ID}\",
    \"organization_name\": \"E2E Governance Org\",
    \"callable_target_bindings\": [
      {\"callable_key\": \"${MODEL_KEY}\", \"enabled\": true},
      {\"callable_key\": \"${SECONDARY_MODEL_KEY}\", \"enabled\": true}
    ]
  }"
echo

echo "== Organization asset access =="
curl -sS "${BASE}/ui/api/organizations/${ORG_ID}/asset-access?include_targets=false" "${auth_header[@]}"
echo

echo "== Create team =="
curl -sS -X POST "${BASE}/ui/api/teams" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"team_id\": \"${TEAM_ID}\",
    \"team_alias\": \"E2E Governance Team\",
    \"organization_id\": \"${ORG_ID}\"
  }"
echo

echo "== Restrict team to one asset =="
curl -sS -X PUT "${BASE}/ui/api/teams/${TEAM_ID}/asset-access" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"mode\": \"restrict\",
    \"selected_callable_keys\": [\"${MODEL_KEY}\"]
  }"
echo

echo "== Team asset visibility =="
curl -sS "${BASE}/ui/api/teams/${TEAM_ID}/asset-visibility" "${auth_header[@]}"
echo

echo "== Create key inheriting team access =="
KEY_JSON="$(curl -sS -X POST "${BASE}/ui/api/keys" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"key_name\": \"E2E Governance Key\",
    \"team_id\": \"${TEAM_ID}\"
  }")"
echo "${KEY_JSON}"
echo

KEY_HASH="$(printf '%s' "${KEY_JSON}" | jq -r '.token')"
RAW_KEY="$(printf '%s' "${KEY_JSON}" | jq -r '.raw_key')"

echo "== /v1/models for key =="
curl -sS "${BASE}/v1/models" -H "Authorization: Bearer ${RAW_KEY}"
echo

echo "== Restrict runtime user if the user profile exists =="
curl -sS -X PUT "${BASE}/ui/api/users/${USER_ID}/asset-access" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"mode\": \"restrict\",
    \"selected_callable_keys\": [\"${MODEL_KEY}\"]
  }" || true
echo

echo "== User asset visibility =="
curl -sS "${BASE}/ui/api/users/${USER_ID}/asset-visibility" "${auth_header[@]}" || true
echo

echo "== Callable-target migration report =="
curl -sS "${BASE}/ui/api/callable-target-migration/report" "${auth_header[@]}"
echo

echo "== Callable-target migration backfill =="
curl -sS -X POST "${BASE}/ui/api/callable-target-migration/backfill" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d '{"rollout_states":["needs_org_bootstrap","needs_scope_backfill"]}'
echo
