#!/usr/bin/env bash
set -euo pipefail

BASE="http://localhost:8000"
MASTER_KEY="sk-deltallm-master-2024-secure-key-prod"
H_MASTER="X-Master-Key: $MASTER_KEY"
H_JSON="Content-Type: application/json"
PASS=0
FAIL=0

echo "============================================="
echo "  SCENARIO 8: Setup Self-Service Environment"
echo "  Create org, team with self-service policy,"
echo "  developer user, and team membership."
echo "============================================="
echo ""

echo "--- 8.0: Cleanup previous self-service test data ---"
psql "$DATABASE_URL" -c "
DELETE FROM deltallm_verificationtoken WHERE team_id IN (
  SELECT team_id FROM deltallm_teamtable WHERE team_alias LIKE 'ss-%'
);
DELETE FROM deltallm_teammembership WHERE account_id IN (
  SELECT account_id FROM deltallm_platformaccount WHERE email LIKE '%@ss-test.com'
);
DELETE FROM deltallm_organizationmembership WHERE account_id IN (
  SELECT account_id FROM deltallm_platformaccount WHERE email LIKE '%@ss-test.com'
);
DELETE FROM deltallm_platformsession WHERE account_id IN (
  SELECT account_id FROM deltallm_platformaccount WHERE email LIKE '%@ss-test.com'
);
DELETE FROM deltallm_platformaccount WHERE email LIKE '%@ss-test.com';
DELETE FROM deltallm_teamtable WHERE team_alias LIKE 'ss-%';
" 2>/dev/null || true
echo "  Cleanup done"

echo "--- 8.1: Create organization ---"
ORG_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/organizations" -d '{
  "organization_name": "SS-Test-Org",
  "rpm_limit": 100,
  "tpm_limit": 100000
}')
SS_ORG_ID=$(echo "$ORG_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['organization_id'])")
echo "  Created org: $SS_ORG_ID"

echo "--- 8.2: Grant all models to org ---"
curl -s -X PUT -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/organizations/$SS_ORG_ID/asset-access" -d '{
  "mode": "grant",
  "select_all_selectable": true
}' > /dev/null
echo "  Granted models"

echo "--- 8.3: Create team WITH self-service enabled ---"
TEAM_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams" -d "{
  \"team_alias\": \"ss-enabled-team\",
  \"organization_id\": \"$SS_ORG_ID\",
  \"rpm_limit\": 50,
  \"tpm_limit\": 50000,
  \"self_service_keys_enabled\": true,
  \"self_service_max_keys_per_user\": 3,
  \"self_service_budget_ceiling\": 50.0,
  \"self_service_require_expiry\": true,
  \"self_service_max_expiry_days\": 30
}")
SS_TEAM_ID=$(echo "$TEAM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['team_id'])")
echo "  Created team: $SS_TEAM_ID"

SS_ENABLED=$(echo "$TEAM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('self_service_keys_enabled'))")
if [ "$SS_ENABLED" = "True" ]; then
  echo "  self_service_keys_enabled = True: PASS"
  PASS=$((PASS+1))
else
  echo "  self_service_keys_enabled = $SS_ENABLED: FAIL (expected True)"
  FAIL=$((FAIL+1))
fi

echo "--- 8.4: Create team WITHOUT self-service ---"
TEAM2_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams" -d "{
  \"team_alias\": \"ss-disabled-team\",
  \"organization_id\": \"$SS_ORG_ID\",
  \"rpm_limit\": 50,
  \"tpm_limit\": 50000,
  \"self_service_keys_enabled\": false
}")
SS_TEAM_DISABLED_ID=$(echo "$TEAM2_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['team_id'])")
echo "  Created team (disabled): $SS_TEAM_DISABLED_ID"

echo "--- 8.5: Create developer user account ---"
DEV_EMAIL="developer@ss-test.com"
DEV_PASSWORD="Dev1234!"
curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/rbac/accounts" -d "{
  \"email\": \"$DEV_EMAIL\",
  \"password\": \"$DEV_PASSWORD\",
  \"role\": \"org_user\"
}" > /dev/null
DEV_ACCOUNT_ID=$(curl -s -H "$H_MASTER" "$BASE/ui/api/rbac/accounts" | python3 -c "
import sys,json
for a in json.load(sys.stdin):
    if a['email']=='$DEV_EMAIL':
        print(a['account_id'])
        break
")
echo "  Created developer: $DEV_ACCOUNT_ID ($DEV_EMAIL)"

echo "--- 8.6: Create viewer user account (no self-service perm) ---"
VIEWER_EMAIL="viewer@ss-test.com"
VIEWER_PASSWORD="View1234!"
curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/rbac/accounts" -d "{
  \"email\": \"$VIEWER_EMAIL\",
  \"password\": \"$VIEWER_PASSWORD\",
  \"role\": \"org_user\"
}" > /dev/null
VIEWER_ACCOUNT_ID=$(curl -s -H "$H_MASTER" "$BASE/ui/api/rbac/accounts" | python3 -c "
import sys,json
for a in json.load(sys.stdin):
    if a['email']=='$VIEWER_EMAIL':
        print(a['account_id'])
        break
")
echo "  Created viewer: $VIEWER_ACCOUNT_ID ($VIEWER_EMAIL)"

echo "--- 8.7: Add org memberships (must be done BEFORE team memberships) ---"
curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/rbac/organization-memberships" -d "{
  \"account_id\": \"$DEV_ACCOUNT_ID\",
  \"organization_id\": \"$SS_ORG_ID\",
  \"role\": \"org_member\"
}" > /dev/null
curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/rbac/organization-memberships" -d "{
  \"account_id\": \"$VIEWER_ACCOUNT_ID\",
  \"organization_id\": \"$SS_ORG_ID\",
  \"role\": \"org_member\"
}" > /dev/null
echo "  Org memberships added"

echo "--- 8.8: Add developer to enabled team as team_developer ---"
ADD_DEV_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams/$SS_TEAM_ID/members" -d "{
  \"user_id\": \"$DEV_ACCOUNT_ID\",
  \"user_role\": \"team_developer\"
}")
echo "  Added developer to ss-enabled-team: $ADD_DEV_RESP"

echo "--- 8.9: Add developer to disabled team as team_developer ---"
curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams/$SS_TEAM_DISABLED_ID/members" -d "{
  \"user_id\": \"$DEV_ACCOUNT_ID\",
  \"user_role\": \"team_developer\"
}" > /dev/null
echo "  Added developer to ss-disabled-team"

echo "--- 8.10: Add viewer to enabled team as team_viewer ---"
curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams/$SS_TEAM_ID/members" -d "{
  \"user_id\": \"$VIEWER_ACCOUNT_ID\",
  \"user_role\": \"team_viewer\"
}" > /dev/null
echo "  Added viewer to ss-enabled-team"

echo "--- 8.11: Login as developer and get session cookie ---"
LOGIN_RESP=$(curl -s -c /tmp/ss_dev_cookies.txt -X POST -H "$H_JSON" "$BASE/auth/internal/login" -d "{
  \"email\": \"$DEV_EMAIL\",
  \"password\": \"$DEV_PASSWORD\"
}")
LOGIN_OK=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('account_id') else 'fail')" 2>/dev/null || echo "fail")
if [ "$LOGIN_OK" = "ok" ]; then
  echo "  Developer login: PASS"
  PASS=$((PASS+1))
else
  echo "  Developer login: FAIL"
  echo "  Response: $LOGIN_RESP"
  FAIL=$((FAIL+1))
fi

echo "--- 8.12: Verify developer session has key.create_self ---"
ME_RESP=$(curl -s -b /tmp/ss_dev_cookies.txt "$BASE/auth/me")
HAS_SELF=$(echo "$ME_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
perms=d.get('effective_permissions',[])
print('yes' if 'key.create_self' in perms else 'no')
")
if [ "$HAS_SELF" = "yes" ]; then
  echo "  key.create_self permission: PASS"
  PASS=$((PASS+1))
else
  echo "  key.create_self permission: FAIL"
  echo "  Permissions: $(echo "$ME_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('effective_permissions',[]))")"
  echo "  Team memberships: $(echo "$ME_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('team_memberships',[]))")"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 8.13: Save self-service test env ---"
cat > /tmp/ss_test_env.sh << EOF
export SS_ORG_ID="$SS_ORG_ID"
export SS_TEAM_ID="$SS_TEAM_ID"
export SS_TEAM_DISABLED_ID="$SS_TEAM_DISABLED_ID"
export DEV_ACCOUNT_ID="$DEV_ACCOUNT_ID"
export DEV_EMAIL="$DEV_EMAIL"
export DEV_PASSWORD="$DEV_PASSWORD"
export VIEWER_ACCOUNT_ID="$VIEWER_ACCOUNT_ID"
export VIEWER_EMAIL="$VIEWER_EMAIL"
export VIEWER_PASSWORD="$VIEWER_PASSWORD"
export BASE="$BASE"
export MASTER_KEY="$MASTER_KEY"
EOF
echo "  Saved to /tmp/ss_test_env.sh"

echo ""
echo "============================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "============================================="

[ "$FAIL" -eq 0 ] || exit 1
