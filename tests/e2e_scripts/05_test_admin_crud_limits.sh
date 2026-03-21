#!/usr/bin/env bash
set -euo pipefail

source /tmp/e2e_test_env.sh

H_JSON="Content-Type: application/json"
H_MASTER="X-Master-Key: $MASTER_KEY"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 5: Admin CRUD for Multi-Window Limits"
echo "  Verify GET/UPDATE endpoints expose rph/rpd/tpd"
echo "============================================="
echo ""

echo "--- Test 5.1: GET org should show rph/rpd/tpd fields ---"
ORG_RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/organizations/$E2E_ORG_ID")
echo "$ORG_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
fields = ['rph_limit', 'rpd_limit', 'tpd_limit']
for f in fields:
    v = d.get(f)
    print(f'  {f} = {v}')
"
RPH=$(echo "$ORG_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rph_limit',''))")
if [ "$RPH" = "500" ]; then
  echo "  Org rph_limit = 500: PASS"
  PASS=$((PASS+1))
else
  echo "  Org rph_limit = '$RPH': FAIL (expected 500)"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 5.2: UPDATE org limits ---"
UPDATE_ORG=$(curl -s -X PUT -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/organizations/$E2E_ORG_ID" -d '{
  "rph_limit": 999,
  "rpd_limit": 9999,
  "tpd_limit": 999999
}')
echo "$UPDATE_ORG" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Updated org: rph_limit={d.get(\"rph_limit\")}, rpd_limit={d.get(\"rpd_limit\")}, tpd_limit={d.get(\"tpd_limit\")}')
"
NEW_RPH=$(echo "$UPDATE_ORG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rph_limit',''))")
if [ "$NEW_RPH" = "999" ]; then
  echo "  Org rph_limit updated to 999: PASS"
  PASS=$((PASS+1))
else
  echo "  Org rph_limit = '$NEW_RPH': FAIL (expected 999)"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 5.3: GET team should show rph/rpd/tpd fields ---"
TEAM_RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/teams/$E2E_TEAM_ID")
echo "$TEAM_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
fields = ['rpm_limit', 'tpm_limit', 'rph_limit', 'rpd_limit', 'tpd_limit']
for f in fields:
    print(f'  {f} = {d.get(f)}')
"
T_RPH=$(echo "$TEAM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rph_limit',''))")
if [ "$T_RPH" = "200" ]; then
  echo "  Team rph_limit = 200: PASS"
  PASS=$((PASS+1))
else
  echo "  Team rph_limit = '$T_RPH': FAIL (expected 200)"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 5.4: UPDATE team limits ---"
UPDATE_TEAM=$(curl -s -X PUT -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams/$E2E_TEAM_ID" -d '{
  "rph_limit": 300,
  "rpd_limit": 3000,
  "tpd_limit": 300000
}')
echo "$UPDATE_TEAM" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Updated team: rph_limit={d.get(\"rph_limit\")}, rpd_limit={d.get(\"rpd_limit\")}, tpd_limit={d.get(\"tpd_limit\")}')
"
NT_RPH=$(echo "$UPDATE_TEAM" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rph_limit',''))")
if [ "$NT_RPH" = "300" ]; then
  echo "  Team rph_limit updated to 300: PASS"
  PASS=$((PASS+1))
else
  echo "  Team rph_limit = '$NT_RPH': FAIL (expected 300)"
  FAIL=$((FAIL+1))
fi
echo ""

echo "--- Test 5.5: GET key should show rph/rpd/tpd fields ---"
KEY_RESP=$(curl -s -H "$H_MASTER" "$BASE/ui/api/keys" | python3 -c "
import sys, json
data = json.load(sys.stdin)['data']
for k in data:
    if k['token'] == '$E2E_TOKEN_HASH':
        fields = ['rpm_limit', 'tpm_limit', 'rph_limit', 'rpd_limit', 'tpd_limit']
        for f in fields:
            print(f'  {f} = {k.get(f)}')
        break
")
echo "$KEY_RESP"
echo ""

echo "--- Test 5.6: UPDATE key limits ---"
UPDATE_KEY=$(curl -s -X PUT -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys/$E2E_TOKEN_HASH" -d '{
  "rph_limit": 15,
  "rpd_limit": 100,
  "tpd_limit": 100000
}')
echo "$UPDATE_KEY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'  Updated key: rph_limit={d.get(\"rph_limit\")}, rpd_limit={d.get(\"rpd_limit\")}, tpd_limit={d.get(\"tpd_limit\")}')
"
NK_RPH=$(echo "$UPDATE_KEY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('rph_limit',''))")
if [ "$NK_RPH" = "15" ]; then
  echo "  Key rph_limit updated to 15: PASS"
  PASS=$((PASS+1))
else
  echo "  Key rph_limit = '$NK_RPH': FAIL (expected 15)"
  FAIL=$((FAIL+1))
fi
echo ""

echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
