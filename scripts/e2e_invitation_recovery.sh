#!/bin/zsh
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
require_bin docker

BASE="${BASE:-http://127.0.0.1:4002}"
MASTER_KEY="${MASTER_KEY:-${DELTALLM_MASTER_KEY:-}}"
RUN_ID="${RUN_ID:-$(date +%s)}"

if [[ -z "$MASTER_KEY" ]]; then
  echo "MASTER_KEY or DELTALLM_MASTER_KEY is required" >&2
  exit 1
fi

AUTH_HEADER=(-H "Authorization: Bearer ${MASTER_KEY}")
JSON_HEADER=(-H "Content-Type: application/json")
ORG_COOKIE="$(mktemp)"
TEAM_COOKIE="$(mktemp)"
RESET_COOKIE="$(mktemp)"
trap 'rm -f "$ORG_COOKIE" "$TEAM_COOKIE" "$RESET_COOKIE"' EXIT

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

expect_json_field() {
  local jq_filter="$1"
  local expected="$2"
  local label="$3"
  local actual
  actual="$(jq -r "$jq_filter" <<<"$BODY")"
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: ${label} expected '${expected}', got '${actual}'" >&2
    printf '%s\n' "$BODY" >&2
    exit 1
  fi
  echo "PASS: ${label}"
}

sql_escape() {
  local value="$1"
  printf "%s" "${value//\'/\'\'}"
}

query_db() {
  docker compose exec -T db psql -U postgres -d deltallm -At -c "$1"
}

extract_email_url() {
  local template_key="$1"
  local recipient="$2"
  local json_key="$3"
  local template_sql recipient_sql
  template_sql="$(sql_escape "$template_key")"
  recipient_sql="$(sql_escape "$recipient")"
  query_db "SELECT COALESCE(payload_json->>'${json_key}', '') FROM deltallm_emailoutbox WHERE template_key = '${template_sql}' AND to_addresses @> ARRAY['${recipient_sql}']::text[] ORDER BY created_at DESC LIMIT 1;"
}

extract_token_from_url() {
  local url="$1"
  printf '%s' "${url##*token=}"
}

resolve_principal() {
  local email="$1"
  request "${AUTH_HEADER[@]}" "${BASE}/ui/api/principals?search=$(jq -rn --arg value "$email" '$value|@uri')"
  expect_code 200 "lookup principal ${email}"
}

ORG_ID="org-invite-${RUN_ID}"
TEAM_ID="team-invite-${RUN_ID}"
ORG_NAME="Invite Org ${RUN_ID}"
TEAM_ALIAS="invite-team-${RUN_ID}"
ORG_EMAIL="invite-org-${RUN_ID}@example.com"
TEAM_EMAIL="invite-team-${RUN_ID}@example.com"
ORG_PASSWORD="InvitePass123!"
TEAM_PASSWORD="TeamPass123!"
RESET_PASSWORD="ResetPass123!"

echo "== Scenario 0: Health check =="
request "$BASE/health/liveliness"
expect_code 200 "service liveliness"

echo "== Scenario 1: Create organization and team =="
ORG_PAYLOAD="$(jq -nc \
  --arg organization_id "$ORG_ID" \
  --arg organization_name "$ORG_NAME" \
  '{organization_id: $organization_id, organization_name: $organization_name, rpm_limit: 100, tpm_limit: 100000}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/organizations" -d "$ORG_PAYLOAD"
expect_code 200 "create organization for invitation flow"

TEAM_PAYLOAD="$(jq -nc \
  --arg team_id "$TEAM_ID" \
  --arg team_alias "$TEAM_ALIAS" \
  --arg organization_id "$ORG_ID" \
  '{team_id: $team_id, team_alias: $team_alias, organization_id: $organization_id, rpm_limit: 50, tpm_limit: 50000}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/teams" -d "$TEAM_PAYLOAD"
expect_code 200 "create team for invitation flow"

echo "== Scenario 2: Invite unknown email directly to organization =="
ORG_INVITE_PAYLOAD="$(jq -nc \
  --arg email "$ORG_EMAIL" \
  --arg organization_id "$ORG_ID" \
  '{email: $email, organization_id: $organization_id, organization_role: "org_member"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/invitations" -d "$ORG_INVITE_PAYLOAD"
expect_code 200 "create organization invitation"
ORG_INVITATION_ID="$(jq -r '.invitation_id' <<<"$BODY")"

ORG_ACCEPT_URL="$(extract_email_url "invite_user" "$ORG_EMAIL" "accept_url")"
if [[ -z "$ORG_ACCEPT_URL" ]]; then
  echo "FAIL: could not resolve organization invitation accept URL from outbox" >&2
  exit 1
fi
ORG_INVITE_TOKEN="$(extract_token_from_url "$ORG_ACCEPT_URL")"

request "$BASE/auth/invitations/${ORG_INVITE_TOKEN}"
expect_code 200 "validate organization invite token"
expect_json_field '.valid' 'true' "organization invite token valid"
expect_json_field '.email' "$ORG_EMAIL" "organization invite email matches"

ORG_ACCEPT_PAYLOAD="$(jq -nc --arg token "$ORG_INVITE_TOKEN" --arg password "$ORG_PASSWORD" '{token: $token, password: $password}')"
request -c "$ORG_COOKIE" -b "$ORG_COOKIE" -X POST "${JSON_HEADER[@]}" "$BASE/auth/invitations/accept" -d "$ORG_ACCEPT_PAYLOAD"
expect_code 200 "accept organization invitation"
expect_json_field '.email' "$ORG_EMAIL" "organization invite login email"

request -b "$ORG_COOKIE" "$BASE/auth/me"
expect_code 200 "session established after organization invite"
expect_json_field '.email' "$ORG_EMAIL" "organization invite session email"

resolve_principal "$ORG_EMAIL"
expect_json_field '.data[0].is_active' 'true' "organization invite account activated"
expect_json_field '.data[0].organization_memberships | any(.organization_id == "'"$ORG_ID"'")' 'true' "organization membership granted"

echo "== Scenario 3: Invite unknown email directly to team =="
TEAM_INVITE_PAYLOAD="$(jq -nc \
  --arg email "$TEAM_EMAIL" \
  --arg team_id "$TEAM_ID" \
  '{email: $email, team_id: $team_id, team_role: "team_viewer"}')"
request -X POST "${AUTH_HEADER[@]}" "${JSON_HEADER[@]}" "$BASE/ui/api/invitations" -d "$TEAM_INVITE_PAYLOAD"
expect_code 200 "create team invitation"

TEAM_ACCEPT_URL="$(extract_email_url "invite_user" "$TEAM_EMAIL" "accept_url")"
if [[ -z "$TEAM_ACCEPT_URL" ]]; then
  echo "FAIL: could not resolve team invitation accept URL from outbox" >&2
  exit 1
fi
TEAM_INVITE_TOKEN="$(extract_token_from_url "$TEAM_ACCEPT_URL")"

request "$BASE/auth/invitations/${TEAM_INVITE_TOKEN}"
expect_code 200 "validate team invite token"
expect_json_field '.valid' 'true' "team invite token valid"
expect_json_field '.email' "$TEAM_EMAIL" "team invite email matches"

TEAM_ACCEPT_PAYLOAD="$(jq -nc --arg token "$TEAM_INVITE_TOKEN" --arg password "$TEAM_PASSWORD" '{token: $token, password: $password}')"
request -c "$TEAM_COOKIE" -b "$TEAM_COOKIE" -X POST "${JSON_HEADER[@]}" "$BASE/auth/invitations/accept" -d "$TEAM_ACCEPT_PAYLOAD"
expect_code 200 "accept team invitation"

request -b "$TEAM_COOKIE" "$BASE/auth/me"
expect_code 200 "session established after team invite"
expect_json_field '.email' "$TEAM_EMAIL" "team invite session email"

resolve_principal "$TEAM_EMAIL"
expect_json_field '.data[0].is_active' 'true' "team invite account activated"
expect_json_field '.data[0].organization_memberships | any(.organization_id == "'"$ORG_ID"'")' 'true' "team invite inherited organization membership"
expect_json_field '.data[0].team_memberships | any(.team_id == "'"$TEAM_ID"'")' 'true' "team membership granted"

echo "== Scenario 4: Forgot-password stays generic =="
FORGOT_PAYLOAD="$(jq -nc --arg email "$TEAM_EMAIL" '{email: $email}')"
request -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/forgot-password" -d "$FORGOT_PAYLOAD"
expect_code 200 "forgot-password existing account"
expect_json_field '.requested' 'true' "existing forgot-password response"

MISSING_PAYLOAD="$(jq -nc --arg email "missing-${RUN_ID}@example.com" '{email: $email}')"
request -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/forgot-password" -d "$MISSING_PAYLOAD"
expect_code 200 "forgot-password missing account"
expect_json_field '.requested' 'true' "missing forgot-password response"

RESET_URL="$(extract_email_url "reset_password" "$TEAM_EMAIL" "reset_url")"
if [[ -z "$RESET_URL" ]]; then
  echo "FAIL: could not resolve reset URL from outbox" >&2
  exit 1
fi
RESET_TOKEN="$(extract_token_from_url "$RESET_URL")"

request "$BASE/auth/internal/reset-password/${RESET_TOKEN}"
expect_code 200 "validate reset token"
expect_json_field '.valid' 'true' "reset token valid"
expect_json_field '.email' "$TEAM_EMAIL" "reset token email matches"

RESET_PAYLOAD="$(jq -nc --arg token "$RESET_TOKEN" --arg new_password "$RESET_PASSWORD" '{token: $token, new_password: $new_password}')"
request -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/reset-password" -d "$RESET_PAYLOAD"
expect_code 200 "reset password"
expect_json_field '.changed' 'true' "password reset response"

request -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/reset-password" -d "$RESET_PAYLOAD"
expect_code 400 "reject reset token replay"

OLD_LOGIN_PAYLOAD="$(jq -nc --arg email "$TEAM_EMAIL" --arg password "$TEAM_PASSWORD" '{email: $email, password: $password}')"
request -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/login" -d "$OLD_LOGIN_PAYLOAD"
expect_code 401 "old password rejected after reset"

NEW_LOGIN_PAYLOAD="$(jq -nc --arg email "$TEAM_EMAIL" --arg password "$RESET_PASSWORD" '{email: $email, password: $password}')"
request -c "$RESET_COOKIE" -b "$RESET_COOKIE" -X POST "${JSON_HEADER[@]}" "$BASE/auth/internal/login" -d "$NEW_LOGIN_PAYLOAD"
expect_code 200 "new password login succeeds"
expect_json_field '.email' "$TEAM_EMAIL" "reset password login email"

echo "== Scenario 5: Invitation lifecycle visible in admin API =="
request "${AUTH_HEADER[@]}" "${BASE}/ui/api/invitations?search=$(jq -rn --arg value "$ORG_EMAIL" '$value|@uri')"
expect_code 200 "list invitations for organization email"
expect_json_field '.data | any(.invitation_id == "'"$ORG_INVITATION_ID"'" and .status == "accepted")' 'true' "organization invitation marked accepted"

echo "PASS: invitation and password recovery scenarios completed"
