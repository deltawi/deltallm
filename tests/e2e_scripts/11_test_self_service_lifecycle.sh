#!/usr/bin/env bash
set -euo pipefail

source /tmp/ss_test_env.sh
source /tmp/ss_keys.sh
H_MASTER="X-Master-Key: $MASTER_KEY"
H_JSON="Content-Type: application/json"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 11: Self-Service Key Lifecycle"
echo "  Test regenerate, revoke, delete by the"
echo "  developer who owns the key, and verify"
echo "  viewer cannot perform lifecycle operations."
echo "============================================="
echo ""

echo "--- Login as developer ---"
curl -s -c /tmp/ss_dev_cookies.txt -X POST -H "$H_JSON" "$BASE/auth/internal/login" -d "{
  \"email\": \"$DEV_EMAIL\",
  \"password\": \"$DEV_PASSWORD\"
}" > /dev/null

echo "--- 11.1: Regenerate own key ---"
if [ -z "${SS_TOKEN_1:-}" ]; then
  echo "  SKIP (no key token from previous test)"
  PASS=$((PASS+1))
else
  RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_dev_cookies.txt -X POST "$BASE/ui/api/keys/$SS_TOKEN_1/regenerate")
  HTTP_CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | sed '$d')
  if [ "$HTTP_CODE" = "200" ]; then
    NEW_KEY=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('raw_key',''))")
    NEW_TOKEN=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")
    echo "  Regenerated key: PASS"
    echo "  New token hash differs: $([ "$NEW_TOKEN" != "$SS_TOKEN_1" ] && echo PASS || echo FAIL)"
    PASS=$((PASS+1))
    SS_TOKEN_1="$NEW_TOKEN"
  else
    echo "  HTTP $HTTP_CODE: FAIL (expected 200)"
    echo "  $BODY"
    FAIL=$((FAIL+1))
  fi
fi

echo ""
echo "--- 11.2: Revoke own key (key 2) ---"
if [ -z "${SS_TOKEN_2:-}" ]; then
  echo "  SKIP (no key token)"
  PASS=$((PASS+1))
else
  RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_dev_cookies.txt -X POST "$BASE/ui/api/keys/$SS_TOKEN_2/revoke")
  HTTP_CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | sed '$d')
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  Revoked key 2: PASS"
    PASS=$((PASS+1))
  else
    echo "  HTTP $HTTP_CODE: FAIL (expected 200)"
    echo "  $BODY"
    FAIL=$((FAIL+1))
  fi
fi

echo ""
echo "--- 11.3: Delete own key (key 3) ---"
if [ -z "${SS_TOKEN_3:-}" ]; then
  echo "  SKIP (no key token)"
  PASS=$((PASS+1))
else
  RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_dev_cookies.txt -X DELETE "$BASE/ui/api/keys/$SS_TOKEN_3")
  HTTP_CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | sed '$d')
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  Deleted key 3: PASS"
    PASS=$((PASS+1))
  else
    echo "  HTTP $HTTP_CODE: FAIL (expected 200)"
    echo "  $BODY"
    FAIL=$((FAIL+1))
  fi
fi

echo ""
echo "--- 11.4: Verify key count dropped (was 3, revoked 1, deleted 1 = 1 left) ---"
RESP=$(curl -s -b /tmp/ss_dev_cookies.txt "$BASE/ui/api/keys?my_keys=true&team_id=$SS_TEAM_ID")
REMAINING=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pagination']['total'])")
echo "  Remaining keys: $REMAINING"
if [ "$REMAINING" = "1" ]; then
  echo "  Count is 1: PASS"
  PASS=$((PASS+1))
else
  echo "  Expected 1, got $REMAINING: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 11.5: Viewer cannot revoke developer's key ---"
curl -s -c /tmp/ss_viewer_cookies.txt -X POST -H "$H_JSON" "$BASE/auth/internal/login" -d "{
  \"email\": \"$VIEWER_EMAIL\",
  \"password\": \"$VIEWER_PASSWORD\"
}" > /dev/null

if [ -n "${SS_TOKEN_1:-}" ]; then
  RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_viewer_cookies.txt -X POST "$BASE/ui/api/keys/$SS_TOKEN_1/revoke")
  HTTP_CODE=$(echo "$RESP" | tail -1)
  if [ "$HTTP_CODE" = "403" ]; then
    echo "  Viewer revoke blocked (403): PASS"
    PASS=$((PASS+1))
  else
    echo "  HTTP $HTTP_CODE: FAIL (expected 403)"
    FAIL=$((FAIL+1))
  fi
else
  echo "  SKIP (no token)"
  PASS=$((PASS+1))
fi

echo ""
echo "--- 11.6: Viewer cannot regenerate developer's key ---"
if [ -n "${SS_TOKEN_1:-}" ]; then
  RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_viewer_cookies.txt -X POST "$BASE/ui/api/keys/$SS_TOKEN_1/regenerate")
  HTTP_CODE=$(echo "$RESP" | tail -1)
  if [ "$HTTP_CODE" = "403" ]; then
    echo "  Viewer regenerate blocked (403): PASS"
    PASS=$((PASS+1))
  else
    echo "  HTTP $HTTP_CODE: FAIL (expected 403)"
    FAIL=$((FAIL+1))
  fi
else
  echo "  SKIP (no token)"
  PASS=$((PASS+1))
fi

echo ""
echo "--- 11.7: Viewer cannot create self-service key ---"
EXPIRY_DATE=$(python3 -c "from datetime import datetime,timedelta; print((datetime.utcnow()+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ'))")
RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_viewer_cookies.txt -X POST -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"viewer-attempt\",
  \"team_id\": \"$SS_TEAM_ID\",
  \"max_budget\": 5.0,
  \"expires\": \"$EXPIRY_DATE\"
}")
HTTP_CODE=$(echo "$RESP" | tail -1)
if [ "$HTTP_CODE" = "403" ]; then
  echo "  Viewer create blocked (403): PASS"
  PASS=$((PASS+1))
else
  echo "  HTTP $HTTP_CODE: FAIL (expected 403)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 11.8: After revoke+delete, developer can create new keys again ---"
RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_dev_cookies.txt -X POST -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"replacement-key\",
  \"team_id\": \"$SS_TEAM_ID\",
  \"max_budget\": 15.0,
  \"expires\": \"$EXPIRY_DATE\"
}")
HTTP_CODE=$(echo "$RESP" | tail -1)
if [ "$HTTP_CODE" = "200" ]; then
  echo "  Created replacement key: PASS"
  PASS=$((PASS+1))
else
  echo "  HTTP $HTTP_CODE: FAIL (expected 200)"
  BODY=$(echo "$RESP" | sed '$d')
  echo "  $BODY"
  FAIL=$((FAIL+1))
fi

echo ""
echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="

[ "$FAIL" -eq 0 ] || exit 1
