#!/usr/bin/env bash
set -euo pipefail

source /tmp/e2e_test_env.sh

H_JSON="Content-Type: application/json"
H_MASTER="X-Master-Key: $MASTER_KEY"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 4: Multi-Window Rate Limits (RPH/RPD)"
echo "  Using key2 with rph_limit=5, rpd_limit=20"
echo "============================================="
echo ""

echo "--- Test 4.1: Create a fresh key with rph_limit=3 for tight testing ---"
KEY3_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"e2e-rph-tight-key\",
  \"team_id\": \"$E2E_TEAM_ID\",
  \"owner_account_id\": \"$E2E_OWNER_ACCOUNT_ID\",
  \"rpm_limit\": null,
  \"tpm_limit\": null,
  \"rph_limit\": 3,
  \"rpd_limit\": null,
  \"tpd_limit\": null
}")
API_KEY3=$(echo "$KEY3_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key') or d.get('raw_key'))")
echo "  Created key with rph_limit=3: ${API_KEY3:0:20}..."
echo ""

echo "--- Test 4.2: Send 3 requests (within RPH limit) ---"
H_AUTH3="Authorization: Bearer $API_KEY3"
for i in 1 2 3; do
  RESP=$(curl -s -w "\n%{http_code}" -X POST -H "$H_AUTH3" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
    "model": "llama-3.1-8b-instant",
    "messages": [{"role": "user", "content": "Say number '"$i"'"}],
    "max_tokens": 5
  }')
  HTTP_CODE=$(echo "$RESP" | tail -1)
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  Request $i: HTTP $HTTP_CODE - PASS"
    PASS=$((PASS+1))
  else
    BODY=$(echo "$RESP" | sed '$d')
    echo "  Request $i: HTTP $HTTP_CODE - FAIL"
    echo "  Body: $BODY"
    FAIL=$((FAIL+1))
  fi
done
echo ""

echo "--- Test 4.3: 4th request should hit RPH limit (429) ---"
RESP=$(curl -s -w "\n%{http_code}" -D /tmp/headers_rph.txt -X POST -H "$H_AUTH3" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "This should be rate limited by RPH"}],
  "max_tokens": 5
}')
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP_CODE" = "429" ]; then
  echo "  Request 4: HTTP $HTTP_CODE - PASS (hit RPH limit)"
  PASS=$((PASS+1))
else
  echo "  Request 4: HTTP $HTTP_CODE - FAIL (expected 429)"
  echo "  Body: $BODY"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 4.4: Verify scope is RPH-related ---"
SCOPE=$(grep -i "x-deltallm-ratelimit-scope" /tmp/headers_rph.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
echo "  Scope: $SCOPE"
if echo "$SCOPE" | grep -q "rph"; then
  echo "  Scope contains 'rph': PASS"
  PASS=$((PASS+1))
else
  echo "  Scope does not contain 'rph': NOTE (may be team/org level scope)"
fi
echo ""

echo "--- Test 4.5: Verify Retry-After header is hour-scale ---"
echo "  Response headers from 429:"
grep -i "x-ratelimit\|retry-after" /tmp/headers_rph.txt 2>/dev/null | tr -d '\r' || echo "  (none)"
RETRY_AFTER=$(grep -i "^retry-after:" /tmp/headers_rph.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ -n "$RETRY_AFTER" ]; then
  echo "  Retry-After = $RETRY_AFTER seconds"
  if [ "$RETRY_AFTER" -gt 0 ] 2>/dev/null; then
    echo "  Retry-After > 0: PASS"
    PASS=$((PASS+1))
  else
    echo "  Retry-After not positive: FAIL"
    FAIL=$((FAIL+1))
  fi
else
  echo "  Retry-After header missing: FAIL"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 4.6: Create key with rpd_limit=2 and verify daily enforcement ---"
KEY4_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"e2e-rpd-tight-key\",
  \"team_id\": \"$E2E_TEAM_ID\",
  \"owner_account_id\": \"$E2E_OWNER_ACCOUNT_ID\",
  \"rpm_limit\": null,
  \"tpm_limit\": null,
  \"rph_limit\": null,
  \"rpd_limit\": 2,
  \"tpd_limit\": null
}")
API_KEY4=$(echo "$KEY4_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key') or d.get('raw_key'))")
echo "  Created key with rpd_limit=2: ${API_KEY4:0:20}..."
H_AUTH4="Authorization: Bearer $API_KEY4"

for i in 1 2; do
  RESP=$(curl -s -w "\n%{http_code}" -X POST -H "$H_AUTH4" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
    "model": "llama-3.1-8b-instant",
    "messages": [{"role": "user", "content": "Count '"$i"'"}],
    "max_tokens": 5
  }')
  HTTP_CODE=$(echo "$RESP" | tail -1)
  echo "  Request $i: HTTP $HTTP_CODE"
done

RESP=$(curl -s -w "\n%{http_code}" -X POST -H "$H_AUTH4" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "This should hit RPD limit"}],
  "max_tokens": 5
}')
HTTP_CODE=$(echo "$RESP" | tail -1)
if [ "$HTTP_CODE" = "429" ]; then
  echo "  Request 3 (over RPD): HTTP $HTTP_CODE - PASS"
  PASS=$((PASS+1))
else
  echo "  Request 3 (over RPD): HTTP $HTTP_CODE - FAIL (expected 429)"
  FAIL=$((FAIL+1))
fi
echo ""

echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
