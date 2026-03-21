#!/usr/bin/env bash
set -euo pipefail

source /tmp/ss_test_env.sh
H_MASTER="X-Master-Key: $MASTER_KEY"
H_JSON="Content-Type: application/json"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 12: Self-Service Audit Events"
echo "  Verify audit trail for self-service key"
echo "  operations (create, regenerate, revoke)."
echo "============================================="
echo ""

echo "--- 12.1: Check ADMIN_KEY_SELF_CREATE audit events ---"
RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/audit/events?action=ADMIN_KEY_SELF_CREATE&actor_id=$DEV_ACCOUNT_ID&limit=10")
COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pagination',{}).get('total', len(d.get('events',d.get('data',[])))))" 2>/dev/null || echo "0")
echo "  ADMIN_KEY_SELF_CREATE events: $COUNT"
if [ "$COUNT" -ge 1 ]; then
  echo "  Self-create audit logged: PASS"
  PASS=$((PASS+1))
else
  echo "  Expected >=1, got $COUNT: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 12.2: Check ADMIN_KEY_SELF_ROTATE audit events ---"
RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/audit/events?action=ADMIN_KEY_SELF_ROTATE&actor_id=$DEV_ACCOUNT_ID&limit=10")
COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pagination',{}).get('total', len(d.get('events',d.get('data',[])))))" 2>/dev/null || echo "0")
echo "  ADMIN_KEY_SELF_ROTATE events: $COUNT"
if [ "$COUNT" -ge 1 ]; then
  echo "  Self-rotate audit logged: PASS"
  PASS=$((PASS+1))
else
  echo "  Expected >=1, got $COUNT: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 12.3: Check ADMIN_KEY_SELF_REVOKE audit events ---"
RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/audit/events?action=ADMIN_KEY_SELF_REVOKE&actor_id=$DEV_ACCOUNT_ID&limit=10")
COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pagination',{}).get('total', len(d.get('events',d.get('data',[])))))" 2>/dev/null || echo "0")
echo "  ADMIN_KEY_SELF_REVOKE events: $COUNT"
if [ "$COUNT" -ge 1 ]; then
  echo "  Self-revoke audit logged: PASS"
  PASS=$((PASS+1))
else
  echo "  Expected >=1, got $COUNT: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 12.4: Verify audit event includes key metadata ---"
RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/audit/events?action=ADMIN_KEY_SELF_CREATE&actor_id=$DEV_ACCOUNT_ID&limit=1")
HAS_DATA=$(echo "$RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
events=d.get('events',d.get('data',[]))
if events:
    e=events[0]
    has_actor = bool(e.get('actor_id'))
    has_action = e.get('action') == 'ADMIN_KEY_SELF_CREATE'
    print('yes' if has_actor and has_action else 'no')
else:
    print('no')
")
if [ "$HAS_DATA" = "yes" ]; then
  echo "  Audit event has actor + action metadata: PASS"
  PASS=$((PASS+1))
else
  echo "  Missing metadata: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 12.5: Self-service policy update on team ---"
RESP=$(curl -s -w "\n%{http_code}" -H "$H_MASTER" -X PUT -H "$H_JSON" "$BASE/ui/api/teams/$SS_TEAM_ID" -d "{
  \"self_service_max_keys_per_user\": 5,
  \"self_service_budget_ceiling\": 100.0
}")
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP_CODE" = "200" ]; then
  NEW_MAX=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('self_service_max_keys_per_user',''))")
  NEW_CEIL=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('self_service_budget_ceiling',''))")
  echo "  Policy updated: max_keys=$NEW_MAX ceiling=$NEW_CEIL: PASS"
  PASS=$((PASS+1))
else
  echo "  HTTP $HTTP_CODE: FAIL"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 12.6: Disable self-service and verify create fails ---"
curl -s -H "$H_MASTER" -X PUT -H "$H_JSON" "$BASE/ui/api/teams/$SS_TEAM_ID" -d "{
  \"self_service_keys_enabled\": false
}" > /dev/null

curl -s -c /tmp/ss_dev_cookies.txt -X POST -H "$H_JSON" "$BASE/auth/internal/login" -d "{
  \"email\": \"$DEV_EMAIL\",
  \"password\": \"$DEV_PASSWORD\"
}" > /dev/null

EXPIRY_DATE=$(python3 -c "from datetime import datetime,timedelta; print((datetime.utcnow()+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ'))")
RESP=$(curl -s -w "\n%{http_code}" -b /tmp/ss_dev_cookies.txt -X POST -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"should-fail\",
  \"team_id\": \"$SS_TEAM_ID\",
  \"max_budget\": 5.0,
  \"expires\": \"$EXPIRY_DATE\"
}")
HTTP_CODE=$(echo "$RESP" | tail -1)
if [ "$HTTP_CODE" = "403" ]; then
  echo "  Create after disable blocked (403): PASS"
  PASS=$((PASS+1))
else
  echo "  HTTP $HTTP_CODE: FAIL (expected 403)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 12.7: Re-enable self-service ---"
curl -s -H "$H_MASTER" -X PUT -H "$H_JSON" "$BASE/ui/api/teams/$SS_TEAM_ID" -d "{
  \"self_service_keys_enabled\": true
}" > /dev/null
echo "  Re-enabled"

echo ""
echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="

[ "$FAIL" -eq 0 ] || exit 1
