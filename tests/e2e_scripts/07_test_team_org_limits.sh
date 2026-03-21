#!/usr/bin/env bash
set -euo pipefail

source /tmp/e2e_test_env.sh

H_JSON="Content-Type: application/json"
H_MASTER="X-Master-Key: $MASTER_KEY"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 7: Team & Org Level Rate Limits"
echo "  Verify team-level limits are enforced across keys"
echo "============================================="
echo ""

echo "--- Test 7.1: Create a team with rpm_limit=3 ---"
TEAM_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams" -d "{
  \"team_alias\": \"e2e-strict-team\",
  \"organization_id\": \"$E2E_ORG_ID\",
  \"rpm_limit\": 3,
  \"tpm_limit\": null,
  \"rph_limit\": null,
  \"rpd_limit\": null
}")
STRICT_TEAM=$(echo "$TEAM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['team_id'])")
echo "  Created team: $STRICT_TEAM with rpm_limit=3"
echo ""

echo "--- Test 7.2: Create two keys under this team (no key-level RPM limits) ---"
KEY_A_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"e2e-team-key-A\",
  \"team_id\": \"$STRICT_TEAM\",
  \"owner_account_id\": \"$E2E_OWNER_ACCOUNT_ID\"
}")
KEY_A=$(echo "$KEY_A_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key') or d.get('raw_key'))")
echo "  Key A: ${KEY_A:0:20}..."

KEY_B_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"e2e-team-key-B\",
  \"team_id\": \"$STRICT_TEAM\",
  \"owner_account_id\": \"$E2E_OWNER_ACCOUNT_ID\"
}")
KEY_B=$(echo "$KEY_B_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key') or d.get('raw_key'))")
echo "  Key B: ${KEY_B:0:20}..."
echo ""

echo "--- Test 7.3: Send 2 requests with Key A, 1 with Key B (total = 3 = team limit) ---"
for i in 1 2; do
  RESP=$(curl -s -w "\n%{http_code}" -X POST -H "Authorization: Bearer $KEY_A" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
    "model": "llama-3.1-8b-instant",
    "messages": [{"role": "user", "content": "Key A request '"$i"'"}],
    "max_tokens": 5
  }')
  HTTP_CODE=$(echo "$RESP" | tail -1)
  echo "  Key A request $i: HTTP $HTTP_CODE"
done

RESP=$(curl -s -w "\n%{http_code}" -X POST -H "Authorization: Bearer $KEY_B" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "Key B request 1"}],
  "max_tokens": 5
}')
HTTP_CODE=$(echo "$RESP" | tail -1)
echo "  Key B request 1: HTTP $HTTP_CODE"
echo ""

echo "--- Test 7.4: 4th request (any key) should hit team RPM limit ---"
RESP=$(curl -s -w "\n%{http_code}" -D /tmp/headers_team.txt -X POST -H "Authorization: Bearer $KEY_B" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "Should hit team limit"}],
  "max_tokens": 5
}')
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP_CODE" = "429" ]; then
  echo "  Key B request 2 (over team limit): HTTP 429 - PASS"
  PASS=$((PASS+1))

  SCOPE=$(grep -i "x-deltallm-ratelimit-scope" /tmp/headers_team.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
  echo "  Rate limit scope: $SCOPE"
  if echo "$SCOPE" | grep -q "team"; then
    echo "  Team scope indicated: PASS"
    PASS=$((PASS+1))
  else
    echo "  Team scope not indicated: NOTE (scope=$SCOPE)"
  fi
else
  echo "  Key B request 2: HTTP $HTTP_CODE - FAIL (expected 429)"
  echo "  Body: $BODY"
  FAIL=$((FAIL+1))
fi
echo ""

echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
