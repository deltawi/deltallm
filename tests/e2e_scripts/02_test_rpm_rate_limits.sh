#!/usr/bin/env bash
set -euo pipefail

source /tmp/e2e_test_env.sh

H_JSON="Content-Type: application/json"
H_AUTH="Authorization: Bearer $E2E_API_KEY"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 2: RPM (Requests Per Minute) Rate Limits"
echo "  Key has rpm_limit=3"
echo "============================================="
echo ""

echo "--- Clearing rate-limit counters for clean test ---"
redis-cli KEYS "ratelimit:*" | xargs -r redis-cli DEL > /dev/null 2>&1 || true
echo "  Done"
echo ""

echo "--- Test 2.1: First 3 requests should succeed (within RPM limit) ---"
for i in 1 2 3; do
  RESP=$(curl -s -w "\n%{http_code}" -X POST -H "$H_AUTH" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
    "model": "llama-3.1-8b-instant",
    "messages": [{"role": "user", "content": "Say the number '"$i"' only"}],
    "max_tokens": 5
  }')
  HTTP_CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | sed '$d')
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  Request $i: HTTP $HTTP_CODE - PASS"
    PASS=$((PASS+1))
  else
    echo "  Request $i: HTTP $HTTP_CODE - FAIL (expected 200)"
    echo "  Body: $BODY"
    FAIL=$((FAIL+1))
  fi
done
echo ""

echo "--- Test 2.2: 4th request should be rate-limited (429) ---"
RESP=$(curl -s -w "\n%{http_code}" -D /tmp/headers_429.txt -X POST -H "$H_AUTH" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "This should fail"}],
  "max_tokens": 5
}')
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP_CODE" = "429" ]; then
  echo "  Request 4: HTTP $HTTP_CODE - PASS (correctly rate-limited)"
  PASS=$((PASS+1))
else
  echo "  Request 4: HTTP $HTTP_CODE - FAIL (expected 429)"
  echo "  Body: $BODY"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 2.3: Verify rate-limit headers on 429 response ---"
echo "  Response headers:"
grep -i "x-ratelimit\|retry-after" /tmp/headers_429.txt 2>/dev/null || echo "  (no rate-limit headers found)"

RL_REMAINING=$(grep -i "x-ratelimit-remaining-requests" /tmp/headers_429.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ "$RL_REMAINING" = "0" ]; then
  echo "  x-ratelimit-remaining-requests = 0: PASS"
  PASS=$((PASS+1))
else
  echo "  x-ratelimit-remaining-requests = '$RL_REMAINING': FAIL (expected 0)"
  FAIL=$((FAIL+1))
fi

RL_WARNING=$(grep -i "x-ratelimit-warning" /tmp/headers_429.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ -n "$RL_WARNING" ]; then
  echo "  x-ratelimit-warning = $RL_WARNING: PASS"
  PASS=$((PASS+1))
else
  echo "  x-ratelimit-warning missing: FAIL"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 2.4: Verify 429 response body has error details ---"
ERROR_TYPE=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('type',''))" 2>/dev/null || echo "")
if [ "$ERROR_TYPE" = "rate_limit_error" ]; then
  echo "  Error type = rate_limit_error: PASS"
  PASS=$((PASS+1))
else
  echo "  Error type = '$ERROR_TYPE': FAIL (expected rate_limit_error)"
  FAIL=$((FAIL+1))
fi
echo ""

echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
