#!/usr/bin/env bash
set -euo pipefail

source /tmp/e2e_test_env.sh

H_JSON="Content-Type: application/json"
H_AUTH="Authorization: Bearer $E2E_API_KEY"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 3: Rate Limit Headers & Soft Warnings"
echo "  Key has rpm_limit=3, rph_limit=10, rpd_limit=50"
echo "============================================="
echo ""

echo "--- Test 3.1: First request should return rate-limit headers ---"
curl -s -D /tmp/headers_first.txt -o /tmp/body_first.json -X POST -H "$H_AUTH" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "Say ok"}],
  "max_tokens": 5
}'

echo "  All rate-limit headers:"
grep -i "x-ratelimit\|x-deltallm" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' || echo "  (none found)"
echo ""

RL_LIMIT=$(grep -i "x-ratelimit-limit-requests" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ -n "$RL_LIMIT" ] && [ "$RL_LIMIT" -gt 0 ] 2>/dev/null; then
  echo "  x-ratelimit-limit-requests = $RL_LIMIT: PASS"
  PASS=$((PASS+1))
else
  echo "  x-ratelimit-limit-requests = '$RL_LIMIT': FAIL (expected > 0)"
  FAIL=$((FAIL+1))
fi

RL_REMAINING=$(grep -i "x-ratelimit-remaining-requests" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ -n "$RL_REMAINING" ]; then
  echo "  x-ratelimit-remaining-requests = $RL_REMAINING: PASS"
  PASS=$((PASS+1))
else
  echo "  x-ratelimit-remaining-requests missing: FAIL"
  FAIL=$((FAIL+1))
fi

RL_RESET=$(grep -i "x-ratelimit-reset-requests" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ -n "$RL_RESET" ] && [ "$RL_RESET" -gt 0 ] 2>/dev/null; then
  echo "  x-ratelimit-reset-requests = $RL_RESET: PASS (unix timestamp)"
  PASS=$((PASS+1))
else
  echo "  x-ratelimit-reset-requests = '$RL_RESET': FAIL (expected unix timestamp)"
  FAIL=$((FAIL+1))
fi

SCOPE=$(grep -i "x-deltallm-ratelimit-scope" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ -n "$SCOPE" ]; then
  echo "  x-deltallm-ratelimit-scope = $SCOPE: PASS"
  PASS=$((PASS+1))
else
  echo "  x-deltallm-ratelimit-scope missing: FAIL"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 3.2: Token-based headers are present ---"
TL_LIMIT=$(grep -i "x-ratelimit-limit-tokens" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
TL_REMAINING=$(grep -i "x-ratelimit-remaining-tokens" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
TL_RESET=$(grep -i "x-ratelimit-reset-tokens" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
echo "  x-ratelimit-limit-tokens = $TL_LIMIT"
echo "  x-ratelimit-remaining-tokens = $TL_REMAINING"
echo "  x-ratelimit-reset-tokens = $TL_RESET"

if [ -n "$TL_LIMIT" ] && [ "$TL_LIMIT" -gt 0 ] 2>/dev/null; then
  echo "  Token limit header present: PASS"
  PASS=$((PASS+1))
else
  echo "  Token limit header missing or zero: FAIL"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 3.3: No warning header on first request (usage is low) ---"
WARNING=$(grep -i "x-ratelimit-warning" /tmp/headers_first.txt 2>/dev/null | tr -d '\r' | awk '{print $2}')
if [ -z "$WARNING" ]; then
  echo "  No warning header (usage low): PASS"
  PASS=$((PASS+1))
else
  echo "  Warning header = '$WARNING': NOTE (may already be near limit from prior test)"
fi
echo ""

echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
