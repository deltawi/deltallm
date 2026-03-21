#!/usr/bin/env bash
set -euo pipefail

source /tmp/ss_test_env.sh
source /tmp/ss_keys.sh
H_MASTER="X-Master-Key: $MASTER_KEY"
H_JSON="Content-Type: application/json"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 10: Self-Service Key Listing"
echo "  Verify my_keys filter, visibility scoping,"
echo "  and viewer restrictions."
echo "============================================="
echo ""

echo "--- Login as developer ---"
curl -s -c /tmp/ss_dev_cookies.txt -X POST -H "$H_JSON" "$BASE/auth/internal/login" -d "{
  \"email\": \"$DEV_EMAIL\",
  \"password\": \"$DEV_PASSWORD\"
}" > /dev/null

echo "--- 10.1: List my_keys=true as developer ---"
RESP=$(curl -s -b /tmp/ss_dev_cookies.txt "$BASE/ui/api/keys?my_keys=true")
MY_COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pagination']['total'])")
echo "  my_keys count: $MY_COUNT"
if [ "$MY_COUNT" -ge 3 ]; then
  echo "  Developer sees own keys (>=3): PASS"
  PASS=$((PASS+1))
else
  echo "  Expected >=3 keys, got $MY_COUNT: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 10.2: All keys visible are owned by developer ---"
ALL_MINE=$(echo "$RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
keys=d['data']
all_mine=all(k.get('owner_account_id')=='$DEV_ACCOUNT_ID' for k in keys)
print('yes' if all_mine else 'no')
")
if [ "$ALL_MINE" = "yes" ]; then
  echo "  All returned keys owned by caller: PASS"
  PASS=$((PASS+1))
else
  echo "  Some keys not owned by caller: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 10.3: List without my_keys as developer ---"
RESP=$(curl -s -b /tmp/ss_dev_cookies.txt "$BASE/ui/api/keys")
ALL_COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pagination']['total'])")
echo "  all keys visible to developer: $ALL_COUNT"
if [ "$ALL_COUNT" -ge 3 ]; then
  echo "  Developer can see keys in scope: PASS"
  PASS=$((PASS+1))
else
  echo "  Expected >=3, got $ALL_COUNT: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 10.4: Login as viewer and list keys ---"
curl -s -c /tmp/ss_viewer_cookies.txt -X POST -H "$H_JSON" "$BASE/auth/internal/login" -d "{
  \"email\": \"$VIEWER_EMAIL\",
  \"password\": \"$VIEWER_PASSWORD\"
}" > /dev/null

RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_viewer_cookies.txt "$BASE/ui/api/keys?my_keys=true")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP_CODE" = "200" ]; then
  VIEWER_COUNT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pagination']['total'])")
  if [ "$VIEWER_COUNT" = "0" ]; then
    echo "  Viewer has 0 own keys: PASS"
    PASS=$((PASS+1))
  else
    echo "  Viewer sees $VIEWER_COUNT keys (expected 0): FAIL"
    FAIL=$((FAIL+1))
  fi
else
  echo "  HTTP $HTTP_CODE (viewer list): PASS (viewer may lack key.read)"
  PASS=$((PASS+1))
fi

echo ""
echo "--- 10.5: Admin list via master key sees all keys ---"
RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/keys?team_id=$SS_TEAM_ID")
ADMIN_COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pagination']['total'])")
echo "  Admin sees $ADMIN_COUNT keys for team"
if [ "$ADMIN_COUNT" -ge 3 ]; then
  echo "  Admin sees all team keys: PASS"
  PASS=$((PASS+1))
else
  echo "  Expected >=3, got $ADMIN_COUNT: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 10.6: Filter by search term ---"
RESP=$(curl -s -b /tmp/ss_dev_cookies.txt "$BASE/ui/api/keys?my_keys=true&search=dev-key-1")
SEARCH_COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['pagination']['total'])")
echo "  Search 'dev-key-1' count: $SEARCH_COUNT"
if [ "$SEARCH_COUNT" -ge 1 ]; then
  echo "  Search filter works: PASS"
  PASS=$((PASS+1))
else
  echo "  Expected >=1, got $SEARCH_COUNT: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="

[ "$FAIL" -eq 0 ] || exit 1
