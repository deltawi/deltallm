#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

require_bin() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required binary: $1" >&2
    exit 1
  fi
}

require_bin curl
require_bin jq

BASE="${BASE:-http://127.0.0.1:4002}"
MASTER_KEY="${MASTER_KEY:-${DELTALLM_MASTER_KEY:-}}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
GROQ_API_KEY="${GROQ_API_KEY:-}"
RUN_ID="${RUN_ID:-$(date +%s)}"

if [[ -z "$MASTER_KEY" ]]; then
  echo "MASTER_KEY or DELTALLM_MASTER_KEY is required" >&2
  exit 1
fi
if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi
if [[ -z "$GROQ_API_KEY" ]]; then
  echo "GROQ_API_KEY is required" >&2
  exit 1
fi

AUTH_HEADER=(-H "Authorization: Bearer ${MASTER_KEY}")
JSON_HEADER=(-H "Content-Type: application/json")
DEV_COOKIE="$(mktemp)"
VIEWER_COOKIE="$(mktemp)"
trap 'rm -f "$DEV_COOKIE" "$VIEWER_COOKIE"' EXIT

HTTP_CODE=""
BODY=""

request() {
  local response
  response="$(curl -sS -w $'\n%{http_code}' "$@")"
  HTTP_CODE="$(printf '%s\n' "$response" | tail -n 1)"
  BODY="$(printf '%s\n' "$response" | sed '$d')"
}

expect_code() {
  local expected="$1"
  local label="$2"
  if [[ "$HTTP_CODE" != "$expected" ]]; then
    echo "FAIL: ${label} returned HTTP ${HTTP_CODE}, expected ${expected}" >&2
    if [[ -n "$BODY" ]]; then
      printf '%s\n' "$BODY" >&2
    fi
    exit 1
  fi
  echo "PASS: ${label} (HTTP ${HTTP_CODE})"
}

expect_any_code() {
  local label="$1"
  shift
  local expected
  for expected in "$@"; do
    if [[ "$HTTP_CODE" == "$expected" ]]; then
      echo "PASS: ${label} (HTTP ${HTTP_CODE})"
      return 0
    fi
  done
  echo "FAIL: ${label} returned HTTP ${HTTP_CODE}, expected one of: $*" >&2
  if [[ -n "$BODY" ]]; then
    printf '%s\n' "$BODY" >&2
  fi
  exit 1
}

contains_model() {
  local json="$1"
  local model_name="$2"
  jq -e --arg model_name "$model_name" '.data | any(.id == $model_name)' >/dev/null <<<"$json"
}

contains_deployment() {
  local json="$1"
  local deployment_id="$2"
  jq -e --arg deployment_id "$deployment_id" '.data | any(.deployment_id == $deployment_id)' >/dev/null <<<"$json"
}

OPENAI_MODEL_NAME="e2e-openai-${RUN_ID}"
OPENAI_DEPLOYMENT_ID="${OPENAI_MODEL_NAME}-dep"
GROQ_MODEL_NAME="e2e-groq-${RUN_ID}"
GROQ_DEPLOYMENT_ID="${GROQ_MODEL_NAME}-dep"
ORG_ID="org-e2e-${RUN_ID}"
TEAM_ID="team-e2e-${RUN_ID}"
ORG_NAME="E2E Org ${RUN_ID}"
TEAM_ALIAS="e2e-team-${RUN_ID}"
DEV_EMAIL="developer+${RUN_ID}@ss-test.com"
DEV_PASSWORD="Dev1234!"
VIEWER_EMAIL="viewer+${RUN_ID}@ss-test.com"
VIEWER_PASSWORD="View1234!"

echo "== Scenario 1: Declare OpenAI model via admin API =="
OPENAI_MODEL_PAYLOAD="$(jq -nc \
  --arg deployment_id "$OPENAI_DEPLOYMENT_ID" \
  --arg model_name "$OPENAI_MODEL_NAME" \
  --arg api_key "$OPENAI_API_KEY" \
  '{
    deployment_id: $deployment_id,
    model_name: $model_name,
    deltallm_params: {
      provider: "openai",
      model: "openai/gpt-4o-mini",
      api_base: "https://api.openai.com/v1",
      api_key: $api_key,
      timeout: 60
    },
    model_info: {mode: "chat"}
  }')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/models" -d "$OPENAI_MODEL_PAYLOAD"
expect_code 200 "create OpenAI deployment"

echo "== Scenario 2: Declare Groq model via admin API =="
GROQ_MODEL_PAYLOAD="$(jq -nc \
  --arg deployment_id "$GROQ_DEPLOYMENT_ID" \
  --arg model_name "$GROQ_MODEL_NAME" \
  --arg api_key "$GROQ_API_KEY" \
  '{
    deployment_id: $deployment_id,
    model_name: $model_name,
    deltallm_params: {
      provider: "groq",
      model: "llama-3.1-8b-instant",
      api_base: "https://api.groq.com/openai/v1",
      api_key: $api_key,
      timeout: 60
    },
    model_info: {mode: "chat"}
  }')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/models" -d "$GROQ_MODEL_PAYLOAD"
expect_code 200 "create Groq deployment"

echo "== Scenario 3: Verify both models are publicly visible =="
request "${AUTH_HEADER[@]}" "$BASE/v1/models"
expect_code 200 "list public models with master key"
contains_model "$BODY" "$OPENAI_MODEL_NAME" || { echo "FAIL: ${OPENAI_MODEL_NAME} missing from /v1/models" >&2; exit 1; }
contains_model "$BODY" "$GROQ_MODEL_NAME" || { echo "FAIL: ${GROQ_MODEL_NAME} missing from /v1/models" >&2; exit 1; }
echo "PASS: both E2E models visible"

echo "== Scenario 4: Create organization and grant both models =="
ORG_PAYLOAD="$(jq -nc \
  --arg organization_id "$ORG_ID" \
  --arg organization_name "$ORG_NAME" \
  '{
    organization_id: $organization_id,
    organization_name: $organization_name,
    rpm_limit: 100,
    tpm_limit: 100000
  }')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/organizations" -d "$ORG_PAYLOAD"
expect_code 200 "create organization"

ORG_ACCESS_PAYLOAD="$(jq -nc \
  --arg openai_model "$OPENAI_MODEL_NAME" \
  --arg groq_model "$GROQ_MODEL_NAME" \
  '{
    mode: "grant",
    selected_callable_keys: [$openai_model, $groq_model]
  }')"
request -X PUT "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/organizations/$ORG_ID/asset-access" -d "$ORG_ACCESS_PAYLOAD"
expect_code 200 "grant org access to both models"

echo "== Scenario 5: Create a self-service team =="
TEAM_PAYLOAD="$(jq -nc \
  --arg team_id "$TEAM_ID" \
  --arg team_alias "$TEAM_ALIAS" \
  --arg organization_id "$ORG_ID" \
  '{
    team_id: $team_id,
    team_alias: $team_alias,
    organization_id: $organization_id,
    rpm_limit: 50,
    tpm_limit: 50000,
    self_service_keys_enabled: true,
    self_service_max_keys_per_user: 3,
    self_service_budget_ceiling: 50,
    self_service_require_expiry: true,
    self_service_max_expiry_days: 30
  }')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/teams" -d "$TEAM_PAYLOAD"
expect_code 200 "create self-service team"

echo "== Scenario 6: Create developer and viewer accounts =="
DEV_ACCOUNT_PAYLOAD="$(jq -nc \
  --arg email "$DEV_EMAIL" \
  --arg password "$DEV_PASSWORD" \
  '{email: $email, password: $password, role: "org_user"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/rbac/accounts" -d "$DEV_ACCOUNT_PAYLOAD"
expect_code 200 "create developer account"

VIEWER_ACCOUNT_PAYLOAD="$(jq -nc \
  --arg email "$VIEWER_EMAIL" \
  --arg password "$VIEWER_PASSWORD" \
  '{email: $email, password: $password, role: "org_user"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/rbac/accounts" -d "$VIEWER_ACCOUNT_PAYLOAD"
expect_code 200 "create viewer account"

request "${AUTH_HEADER[@]}" "$BASE/ui/api/rbac/accounts"
expect_code 200 "list accounts"
DEV_ACCOUNT_ID="$(jq -r --arg email "$DEV_EMAIL" '.[] | select(.email == $email) | .account_id' <<<"$BODY")"
VIEWER_ACCOUNT_ID="$(jq -r --arg email "$VIEWER_EMAIL" '.[] | select(.email == $email) | .account_id' <<<"$BODY")"
if [[ -z "$DEV_ACCOUNT_ID" || -z "$VIEWER_ACCOUNT_ID" ]]; then
  echo "FAIL: could not resolve account IDs for developer/viewer" >&2
  exit 1
fi
echo "PASS: resolved developer and viewer account IDs"

echo "== Scenario 7: Attach org and team memberships =="
ORG_MEMBER_PAYLOAD="$(jq -nc \
  --arg account_id "$DEV_ACCOUNT_ID" \
  --arg organization_id "$ORG_ID" \
  '{account_id: $account_id, organization_id: $organization_id, role: "org_member"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/rbac/organization-memberships" -d "$ORG_MEMBER_PAYLOAD"
expect_code 200 "add developer org membership"

ORG_MEMBER_PAYLOAD="$(jq -nc \
  --arg account_id "$VIEWER_ACCOUNT_ID" \
  --arg organization_id "$ORG_ID" \
  '{account_id: $account_id, organization_id: $organization_id, role: "org_member"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/rbac/organization-memberships" -d "$ORG_MEMBER_PAYLOAD"
expect_code 200 "add viewer org membership"

TEAM_MEMBER_PAYLOAD="$(jq -nc \
  --arg user_id "$DEV_ACCOUNT_ID" \
  '{user_id: $user_id, user_role: "team_developer"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/teams/$TEAM_ID/members" -d "$TEAM_MEMBER_PAYLOAD"
expect_code 200 "add developer to team"

TEAM_MEMBER_PAYLOAD="$(jq -nc \
  --arg user_id "$VIEWER_ACCOUNT_ID" \
  '{user_id: $user_id, user_role: "team_viewer"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/teams/$TEAM_ID/members" -d "$TEAM_MEMBER_PAYLOAD"
expect_code 200 "add viewer to team"

echo "== Scenario 8: Login as developer and viewer =="
LOGIN_PAYLOAD="$(jq -nc --arg email "$DEV_EMAIL" --arg password "$DEV_PASSWORD" '{email: $email, password: $password}')"
request -c "$DEV_COOKIE" -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/login" -d "$LOGIN_PAYLOAD"
expect_code 200 "developer login"

LOGIN_PAYLOAD="$(jq -nc --arg email "$VIEWER_EMAIL" --arg password "$VIEWER_PASSWORD" '{email: $email, password: $password}')"
request -c "$VIEWER_COOKIE" -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/login" -d "$LOGIN_PAYLOAD"
expect_code 200 "viewer login"

echo "== Scenario 9: Developer can create a self-service key =="
EXPIRY_DATE="$(python3 - <<'PY'
from datetime import datetime, timedelta
print((datetime.utcnow() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)"
DEV_KEY_PAYLOAD="$(jq -nc \
  --arg team_id "$TEAM_ID" \
  --arg expires "$EXPIRY_DATE" \
  '{
    key_name: "developer-e2e-key",
    team_id: $team_id,
    max_budget: 25,
    expires: $expires
  }')"
request -b "$DEV_COOKIE" -X POST "${JSON_HEADER[@]}" "$BASE/ui/api/keys" -d "$DEV_KEY_PAYLOAD"
expect_code 200 "developer self-service key create"
DEV_RAW_KEY="$(jq -r '.raw_key' <<<"$BODY")"
DEV_TOKEN_HASH="$(jq -r '.token' <<<"$BODY")"
if [[ -z "$DEV_RAW_KEY" || "$DEV_RAW_KEY" == "null" ]]; then
  echo "FAIL: developer key response did not include raw_key" >&2
  exit 1
fi
echo "PASS: developer key created (${DEV_TOKEN_HASH})"

echo "== Scenario 10: Viewer is blocked from self-service key creation =="
VIEWER_KEY_PAYLOAD="$(jq -nc \
  --arg team_id "$TEAM_ID" \
  --arg expires "$EXPIRY_DATE" \
  '{
    key_name: "viewer-e2e-key",
    team_id: $team_id,
    max_budget: 10,
    expires: $expires
  }')"
request -b "$VIEWER_COOKIE" -X POST "${JSON_HEADER[@]}" "$BASE/ui/api/keys" -d "$VIEWER_KEY_PAYLOAD"
expect_code 403 "viewer self-service key create blocked"

echo "== Scenario 11: Developer key sees both models =="
request -H "Authorization: Bearer ${DEV_RAW_KEY}" "$BASE/v1/models"
expect_code 200 "developer key model visibility"
contains_model "$BODY" "$OPENAI_MODEL_NAME" || { echo "FAIL: developer key missing ${OPENAI_MODEL_NAME}" >&2; exit 1; }
contains_model "$BODY" "$GROQ_MODEL_NAME" || { echo "FAIL: developer key missing ${GROQ_MODEL_NAME}" >&2; exit 1; }
echo "PASS: developer key sees both E2E models"

echo "== Scenario 12: Developer key can chat through OpenAI =="
OPENAI_CHAT_PAYLOAD="$(jq -nc \
  --arg model "$OPENAI_MODEL_NAME" \
  '{
    model: $model,
    messages: [{role: "user", content: "Reply with exactly: openai-ok"}],
    temperature: 0,
    max_tokens: 10
  }')"
request -X POST -H "Authorization: Bearer ${DEV_RAW_KEY}" "${JSON_HEADER[@]}" "$BASE/v1/chat/completions" -d "$OPENAI_CHAT_PAYLOAD"
expect_code 200 "developer OpenAI chat"
OPENAI_REPLY="$(jq -r '.choices[0].message.content' <<<"$BODY")"
echo "OpenAI reply: ${OPENAI_REPLY}"

echo "== Scenario 13: Developer key can chat through Groq =="
GROQ_CHAT_PAYLOAD="$(jq -nc \
  --arg model "$GROQ_MODEL_NAME" \
  '{
    model: $model,
    messages: [{role: "user", content: "Reply with exactly: groq-ok"}],
    temperature: 0,
    max_tokens: 10
  }')"
request -X POST -H "Authorization: Bearer ${DEV_RAW_KEY}" "${JSON_HEADER[@]}" "$BASE/v1/chat/completions" -d "$GROQ_CHAT_PAYLOAD"
expect_code 200 "developer Groq chat"
GROQ_REPLY="$(jq -r '.choices[0].message.content' <<<"$BODY")"
echo "Groq reply: ${GROQ_REPLY}"

echo "== Scenario 14: Restrict team access to OpenAI only =="
TEAM_RESTRICT_PAYLOAD="$(jq -nc --arg openai_model "$OPENAI_MODEL_NAME" '{mode: "restrict", selected_callable_keys: [$openai_model]}')"
request -X PUT "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/teams/$TEAM_ID/asset-access" -d "$TEAM_RESTRICT_PAYLOAD"
expect_code 200 "restrict team to OpenAI"

echo "== Scenario 15: Developer key visibility updates after governance change =="
for _ in {1..10}; do
  request -H "Authorization: Bearer ${DEV_RAW_KEY}" "$BASE/v1/models"
  expect_code 200 "developer key model visibility after restriction"
  if contains_model "$BODY" "$OPENAI_MODEL_NAME" && ! contains_model "$BODY" "$GROQ_MODEL_NAME"; then
    echo "PASS: governance restriction propagated"
    break
  fi
  sleep 1
done
if ! contains_model "$BODY" "$OPENAI_MODEL_NAME" || contains_model "$BODY" "$GROQ_MODEL_NAME"; then
  echo "FAIL: developer key visibility did not converge to OpenAI-only" >&2
  printf '%s\n' "$BODY" >&2
  exit 1
fi

echo "== Scenario 16: Developer key is blocked from Groq after restriction =="
request -X POST -H "Authorization: Bearer ${DEV_RAW_KEY}" "${JSON_HEADER[@]}" "$BASE/v1/chat/completions" -d "$GROQ_CHAT_PAYLOAD"
expect_any_code "developer Groq chat blocked after restriction" 403 404

echo
echo "E2E gateway role scenarios completed successfully."
echo "Run ID: ${RUN_ID}"
echo "Organization: ${ORG_ID}"
echo "Team: ${TEAM_ID}"
echo "Developer account: ${DEV_EMAIL}"
echo "Viewer account: ${VIEWER_EMAIL}"
echo "OpenAI model: ${OPENAI_MODEL_NAME}"
echo "Groq model: ${GROQ_MODEL_NAME}"
