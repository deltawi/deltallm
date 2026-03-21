#!/usr/bin/env bash
set -euo pipefail

source /tmp/e2e_test_env.sh

H_JSON="Content-Type: application/json"
H_MASTER="X-Master-Key: $MASTER_KEY"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 6: Cache Invalidation After Key Update"
echo "  Verify limits take effect immediately after admin update"
echo "============================================="
echo ""

echo "--- Test 6.1: Create key with high RPM limit (100) ---"
KEY_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"e2e-cache-test-key\",
  \"team_id\": \"$E2E_TEAM_ID\",
  \"owner_account_id\": \"$E2E_OWNER_ACCOUNT_ID\",
  \"rpm_limit\": 100,
  \"tpm_limit\": null,
  \"rph_limit\": null,
  \"rpd_limit\": null
}")
CACHE_KEY=$(echo "$KEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key') or d.get('raw_key'))")
CACHE_HASH=$(echo "$KEY_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
echo "  Created key: ${CACHE_KEY:0:20}..."
echo "  Hash: ${CACHE_HASH:0:16}..."
echo ""

echo "--- Test 6.2: Send request to warm the cache ---"
RESP=$(curl -s -w "\n%{http_code}" -X POST -H "Authorization: Bearer $CACHE_KEY" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "Warm cache"}],
  "max_tokens": 5
}')
HTTP_CODE=$(echo "$RESP" | tail -1)
echo "  Cache warm request: HTTP $HTTP_CODE"
if [ "$HTTP_CODE" = "200" ]; then
  echo "  Cache warmed: PASS"
  PASS=$((PASS+1))
else
  echo "  Cache warm failed: FAIL"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 6.3: Update key to rpm_limit=1 (should invalidate cache) ---"
UPDATE_RESP=$(curl -s -X PUT -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys/$CACHE_HASH" -d '{
  "rpm_limit": 1
}')
NEW_RPM=$(echo "$UPDATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rpm_limit',''))")
echo "  Updated rpm_limit to: $NEW_RPM"
if [ "$NEW_RPM" = "1" ]; then
  echo "  Admin update confirmed: PASS"
  PASS=$((PASS+1))
else
  echo "  Admin update failed: FAIL"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 6.4: Next request should be rate-limited (old cache should be gone) ---"
RESP=$(curl -s -w "\n%{http_code}" -X POST -H "Authorization: Bearer $CACHE_KEY" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "Should be limited now"}],
  "max_tokens": 5
}')
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP_CODE" = "429" ]; then
  echo "  Request after update: HTTP 429 - PASS (cache invalidated, new limit enforced)"
  PASS=$((PASS+1))
elif [ "$HTTP_CODE" = "200" ]; then
  echo "  Request after update: HTTP 200 - NOTE (first request under new limit succeeded, testing 2nd)"

  RESP2=$(curl -s -w "\n%{http_code}" -X POST -H "Authorization: Bearer $CACHE_KEY" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
    "model": "llama-3.1-8b-instant",
    "messages": [{"role": "user", "content": "This one should definitely be limited"}],
    "max_tokens": 5
  }')
  HTTP_CODE2=$(echo "$RESP2" | tail -1)
  if [ "$HTTP_CODE2" = "429" ]; then
    echo "  2nd request after update: HTTP 429 - PASS (new rpm_limit=1 enforced)"
    PASS=$((PASS+1))
  else
    echo "  2nd request after update: HTTP $HTTP_CODE2 - FAIL (expected 429 with rpm_limit=1)"
    FAIL=$((FAIL+1))
  fi
else
  echo "  Request after update: HTTP $HTTP_CODE - FAIL (unexpected)"
  echo "  Body: $BODY"
  FAIL=$((FAIL+1))
fi
echo ""

echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
