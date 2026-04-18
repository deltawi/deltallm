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
require_bin docker
require_bin jq

BASE="${BASE:-http://127.0.0.1:4002}"
MASTER_KEY="${MASTER_KEY:-${DELTALLM_MASTER_KEY:-}}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
RUN_ID="${RUN_ID:-$(date +%s)}"

if [[ -z "$MASTER_KEY" ]]; then
  echo "MASTER_KEY or DELTALLM_MASTER_KEY is required" >&2
  exit 1
fi
if [[ -z "$OPENAI_API_KEY" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

AUTH_HEADER=(-H "Authorization: Bearer ${MASTER_KEY}")
JSON_HEADER=(-H "Content-Type: application/json")
CHAT_PAYLOAD='{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Reply with exactly: budget-ok"}],"temperature":0,"max_tokens":8}'
SOFT_BUDGET="0.000000000001"

TMP_CONFIG="$(mktemp "$ROOT_DIR/.deltallm-budget-e2e-config.XXXX.yaml")"
TMP_OVERRIDE="$(mktemp "$ROOT_DIR/.deltallm-budget-e2e-compose.XXXX.yaml")"

HTTP_CODE=""
BODY=""

cleanup() {
  set +e
  docker compose up -d deltallm >/dev/null 2>&1 || true
  rm -f "$TMP_CONFIG" "$TMP_OVERRIDE"
}
trap cleanup EXIT

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

psql_scalar() {
  local sql="$1"
  docker compose exec -T db psql -U postgres -d deltallm -Atqc "$sql"
}

wait_for_http_200() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"
  local sleep_seconds="${4:-2}"
  local code=""
  for _ in $(seq 1 "$attempts"); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$url" || true)"
    if [[ "$code" == "200" ]]; then
      echo "PASS: ${label}"
      return 0
    fi
    sleep "$sleep_seconds"
  done
  echo "FAIL: ${label} never became ready" >&2
  exit 1
}

write_temp_config() {
  awk '
    BEGIN {
      inserted = 0
      in_general = 0
    }
    /^general_settings:[[:space:]]*$/ {
      print
      in_general = 1
      next
    }
    in_general && /^[^[:space:]]/ {
      if (!inserted) {
        print "  governance_notifications_enabled: true"
        print "  budget_notifications_enabled: true"
        inserted = 1
      }
      in_general = 0
    }
    {
      print
    }
    END {
      if (in_general && !inserted) {
        print "  governance_notifications_enabled: true"
        print "  budget_notifications_enabled: true"
      }
    }
  ' "$ROOT_DIR/config.yaml" >"$TMP_CONFIG"
}

write_temp_override() {
  cat >"$TMP_OVERRIDE" <<EOF
services:
  deltallm:
    environment:
      DELTALLM_CONFIG_PATH: /app/e2e-budget-config.yaml
    volumes:
      - ${TMP_CONFIG}:/app/e2e-budget-config.yaml:ro
EOF
}

OWNER_EMAIL="budget-owner+${RUN_ID}@example.com"
OWNER_PASSWORD="Budget1234!!"
ORG_ID="org-budget-e2e-${RUN_ID}"
ORG_NAME="Budget E2E ${RUN_ID}"
TEAM_ID="team-budget-e2e-${RUN_ID}"
TEAM_ALIAS="budget-team-${RUN_ID}"
KEY_NAME="budget-alert-key-${RUN_ID}"

echo "== Step 1: Start Docker app with test-only budget notification config =="
write_temp_config
write_temp_override
docker compose -f docker-compose.yaml -f "$TMP_OVERRIDE" up -d --build deltallm
wait_for_http_200 "$BASE/health/liveliness" "app health check"

request "${AUTH_HEADER[@]}" "$BASE/v1/models"
expect_code 200 "list models with master key"
jq -e '.data | any(.id == "gpt-4o-mini")' >/dev/null <<<"$BODY" || {
  echo "FAIL: gpt-4o-mini missing from /v1/models" >&2
  exit 1
}
echo "PASS: gpt-4o-mini available"

echo "== Step 2: Create an owner account, organization, team, and key =="
OWNER_PAYLOAD="$(jq -nc \
  --arg email "$OWNER_EMAIL" \
  --arg password "$OWNER_PASSWORD" \
  '{email: $email, password: $password, role: "org_user"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/rbac/accounts" -d "$OWNER_PAYLOAD"
expect_code 200 "create key owner account"
OWNER_ACCOUNT_ID="$(jq -r '.account_id' <<<"$BODY")"
if [[ -z "$OWNER_ACCOUNT_ID" || "$OWNER_ACCOUNT_ID" == "null" ]]; then
  echo "FAIL: owner account id missing from create response" >&2
  exit 1
fi
echo "PASS: owner account created (${OWNER_ACCOUNT_ID})"

ORG_PAYLOAD="$(jq -nc \
  --arg organization_id "$ORG_ID" \
  --arg organization_name "$ORG_NAME" \
  '{organization_id: $organization_id, organization_name: $organization_name, rpm_limit: 100, tpm_limit: 100000}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/organizations" -d "$ORG_PAYLOAD"
expect_code 200 "create organization"

ORG_ACCESS_PAYLOAD='{"mode":"grant","selected_callable_keys":["gpt-4o-mini"]}'
request -X PUT "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/organizations/$ORG_ID/asset-access" -d "$ORG_ACCESS_PAYLOAD"
expect_code 200 "grant org access to gpt-4o-mini"

TEAM_PAYLOAD="$(jq -nc \
  --arg team_id "$TEAM_ID" \
  --arg team_alias "$TEAM_ALIAS" \
  --arg organization_id "$ORG_ID" \
  '{team_id: $team_id, team_alias: $team_alias, organization_id: $organization_id, rpm_limit: 100, tpm_limit: 100000}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/teams" -d "$TEAM_PAYLOAD"
expect_code 200 "create team"

KEY_PAYLOAD="$(jq -nc \
  --arg key_name "$KEY_NAME" \
  --arg team_id "$TEAM_ID" \
  --arg owner_account_id "$OWNER_ACCOUNT_ID" \
  '{key_name: $key_name, team_id: $team_id, owner_account_id: $owner_account_id, max_budget: 10}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/keys" -d "$KEY_PAYLOAD"
expect_code 200 "create owned api key"
KEY_HASH="$(jq -r '.token' <<<"$BODY")"
RAW_KEY="$(jq -r '.raw_key' <<<"$BODY")"
if [[ -z "$KEY_HASH" || "$KEY_HASH" == "null" || -z "$RAW_KEY" || "$RAW_KEY" == "null" ]]; then
  echo "FAIL: key create response missing token or raw_key" >&2
  exit 1
fi
echo "PASS: key created (${KEY_HASH})"

echo "== Step 3: Seed key soft budget state directly in Postgres =="
BASELINE_COUNT="$(psql_scalar "SELECT COUNT(*) FROM deltallm_emailoutbox WHERE template_key = 'budget_threshold' AND payload_json->>'entity_type' = 'key' AND payload_json->>'entity_id' = '${KEY_HASH}'")"
docker compose exec -T db psql -U postgres -d deltallm -qc \
  "UPDATE deltallm_verificationtoken SET soft_budget = ${SOFT_BUDGET}::double precision, spend = 1, updated_at = NOW() WHERE token = '${KEY_HASH}';"
SEEDED_STATE="$(docker compose exec -T db psql -U postgres -d deltallm -Atqc "SELECT soft_budget || '|' || spend FROM deltallm_verificationtoken WHERE token = '${KEY_HASH}'")"
SEEDED_SOFT_BUDGET="${SEEDED_STATE%%|*}"
SEEDED_SPEND="${SEEDED_STATE#*|}"
awk -v actual="$SEEDED_SOFT_BUDGET" -v expected="$SOFT_BUDGET" 'BEGIN { diff = actual - expected; if (diff < 0) diff = -diff; exit !(diff < 1e-15) }' || {
  echo "FAIL: soft budget seed did not persist" >&2
  exit 1
}
awk -v spend="$SEEDED_SPEND" 'BEGIN { exit !(spend >= 1) }' || {
  echo "FAIL: spend seed did not persist" >&2
  exit 1
}
echo "PASS: key budget state seeded with soft budget ${SOFT_BUDGET} and spend ${SEEDED_SPEND}"

echo "== Step 4: First real gateway call should enqueue one budget threshold notification =="
request -X POST -H "Authorization: Bearer ${RAW_KEY}" "${JSON_HEADER[@]}" "$BASE/v1/chat/completions" -d "$CHAT_PAYLOAD"
expect_code 200 "first chat completion"
COUNT_AFTER_FIRST="$(psql_scalar "SELECT COUNT(*) FROM deltallm_emailoutbox WHERE template_key = 'budget_threshold' AND payload_json->>'entity_type' = 'key' AND payload_json->>'entity_id' = '${KEY_HASH}'")"
EXPECTED_COUNT="$((BASELINE_COUNT + 1))"
if [[ "$COUNT_AFTER_FIRST" != "$EXPECTED_COUNT" ]]; then
  echo "FAIL: expected exactly one queued budget notification after first request" >&2
  exit 1
fi
LATEST_NOTIFICATION="$(docker compose exec -T db psql -U postgres -d deltallm -Atqc "SELECT status || '|' || COALESCE(payload_json->>'entity_type','') || '|' || COALESCE(payload_json->>'entity_id','') || '|' || COALESCE(array_to_string(to_addresses, ','), '') FROM deltallm_emailoutbox WHERE template_key = 'budget_threshold' AND payload_json->>'entity_id' = '${KEY_HASH}' ORDER BY created_at DESC LIMIT 1")"
LATEST_STATUS="${LATEST_NOTIFICATION%%|*}"
LATEST_REMAINDER="${LATEST_NOTIFICATION#*|}"
LATEST_ENTITY_TYPE="${LATEST_REMAINDER%%|*}"
LATEST_REMAINDER="${LATEST_REMAINDER#*|}"
LATEST_ENTITY_ID="${LATEST_REMAINDER%%|*}"
LATEST_RECIPIENTS="${LATEST_REMAINDER#*|}"
if [[ "$LATEST_STATUS" != "queued" || "$LATEST_ENTITY_TYPE" != "key" || "$LATEST_ENTITY_ID" != "$KEY_HASH" || "$LATEST_RECIPIENTS" != "$OWNER_EMAIL" ]]; then
  echo "FAIL: queued notification payload mismatch" >&2
  printf '%s\n' "$LATEST_NOTIFICATION" >&2
  exit 1
fi
echo "PASS: budget notification queued for ${OWNER_EMAIL}"

echo "== Step 5: Second request should be deduped within the TTL window =="
request -X POST -H "Authorization: Bearer ${RAW_KEY}" "${JSON_HEADER[@]}" "$BASE/v1/chat/completions" -d "$CHAT_PAYLOAD"
expect_code 200 "second chat completion"
COUNT_AFTER_SECOND="$(psql_scalar "SELECT COUNT(*) FROM deltallm_emailoutbox WHERE template_key = 'budget_threshold' AND payload_json->>'entity_type' = 'key' AND payload_json->>'entity_id' = '${KEY_HASH}'")"
if [[ "$COUNT_AFTER_SECOND" != "$EXPECTED_COUNT" ]]; then
  echo "FAIL: TTL dedupe did not hold after second request" >&2
  exit 1
fi
echo "PASS: TTL dedupe prevented a duplicate queued notification"

echo "== Step 6: Verify operator outbox summary over the admin API =="
request "${AUTH_HEADER[@]}" "$BASE/ui/api/email/outbox/summary"
expect_code 200 "email outbox summary"
jq -e '.recent | any(.template_key == "budget_threshold" and .status == "queued")' >/dev/null <<<"$BODY" || {
  echo "FAIL: admin outbox summary did not expose the budget notification" >&2
  exit 1
}
echo "PASS: admin outbox summary exposes queued budget notification"

echo
echo "E2E budget threshold notification scenario completed successfully."
echo "Run ID: ${RUN_ID}"
echo "Owner account: ${OWNER_EMAIL}"
echo "Organization: ${ORG_ID}"
echo "Team: ${TEAM_ID}"
echo "Key hash: ${KEY_HASH}"
