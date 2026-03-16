#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-http://localhost:4000}"
MASTER_KEY="${MASTER_KEY:-}"
SERVER_ID="${SERVER_ID:-}"
ORG_ID="${ORG_ID:-org-main}"
TEAM_ID="${TEAM_ID:-team-ops}"
USER_ID="${USER_ID:-user-1}"

if [[ -z "$MASTER_KEY" ]]; then
  echo "MASTER_KEY is required"
  exit 1
fi

if [[ -z "$SERVER_ID" ]]; then
  echo "SERVER_ID is required"
  exit 1
fi

auth_header=(-H "Authorization: Bearer ${MASTER_KEY}")
json_header=(-H "Content-Type: application/json")

echo "== MCP server detail (enabled rows only) =="
curl -sS "${BASE}/ui/api/mcp-servers/${SERVER_ID}" "${auth_header[@]}"
echo

echo "== MCP server detail including disabled rows =="
curl -sS "${BASE}/ui/api/mcp-servers/${SERVER_ID}?include_disabled=true" "${auth_header[@]}"
echo

echo "== Upsert organization binding =="
curl -sS -X POST "${BASE}/ui/api/mcp-bindings" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"server_id\": \"${SERVER_ID}\",
    \"scope_type\": \"organization\",
    \"scope_id\": \"${ORG_ID}\",
    \"tool_allowlist\": [\"search\"]
  }"
echo

echo "== Restrict team scope =="
curl -sS -X POST "${BASE}/ui/api/mcp-scope-policies" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"scope_type\": \"team\",
    \"scope_id\": \"${TEAM_ID}\",
    \"mode\": \"restrict\"
  }"
echo

echo "== Upsert user binding =="
curl -sS -X POST "${BASE}/ui/api/mcp-bindings" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d "{
    \"server_id\": \"${SERVER_ID}\",
    \"scope_type\": \"user\",
    \"scope_id\": \"${USER_ID}\",
    \"tool_allowlist\": [\"search\"]
  }"
echo

echo "== MCP migration report =="
curl -sS "${BASE}/ui/api/mcp-migration/report" "${auth_header[@]}"
echo

echo "== MCP migration backfill =="
curl -sS -X POST "${BASE}/ui/api/mcp-migration/backfill" \
  "${auth_header[@]}" "${json_header[@]}" \
  -d '{"rollout_states":["needs_org_bootstrap","needs_scope_backfill"]}'
echo
