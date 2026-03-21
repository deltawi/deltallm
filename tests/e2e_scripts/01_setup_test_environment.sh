#!/usr/bin/env bash
set -euo pipefail

BASE="http://localhost:8000"
MASTER_KEY="sk-deltallm-master-2024-secure-key-prod"
H_MASTER="X-Master-Key: $MASTER_KEY"
H_JSON="Content-Type: application/json"

echo "============================================="
echo "  SCENARIO 1: Setup Test Environment"
echo "============================================="
echo ""

echo "--- Step 1: Create test organization ---"
ORG_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/organizations" -d '{
  "organization_name": "E2E-Test-Org",
  "rpm_limit": 100,
  "tpm_limit": 100000,
  "rph_limit": 500,
  "rpd_limit": 5000,
  "tpd_limit": 500000
}')
echo "$ORG_RESP" | python3 -m json.tool
ORG_ID=$(echo "$ORG_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['organization_id'])")
echo "Created org: $ORG_ID"
echo ""

echo "--- Step 2: Create test team under org ---"
TEAM_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/teams" -d "{
  \"team_alias\": \"e2e-test-team\",
  \"organization_id\": \"$ORG_ID\",
  \"rpm_limit\": 50,
  \"tpm_limit\": 50000,
  \"rph_limit\": 200,
  \"rpd_limit\": 2000,
  \"tpd_limit\": 200000
}")
echo "$TEAM_RESP" | python3 -m json.tool
TEAM_ID=$(echo "$TEAM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['team_id'])")
echo "Created team: $TEAM_ID"
echo ""

OWNER_ACCOUNT_ID="71d172c4-d017-4ae2-996c-30df1c47ff5c"

echo "--- Step 3: Create API key with RPM=3 (for rate limit testing) ---"
KEY_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"e2e-rpm-test-key\",
  \"team_id\": \"$TEAM_ID\",
  \"owner_account_id\": \"$OWNER_ACCOUNT_ID\",
  \"rpm_limit\": 3,
  \"tpm_limit\": 10000,
  \"rph_limit\": 10,
  \"rpd_limit\": 50,
  \"tpd_limit\": 50000
}")
echo "$KEY_RESP" | python3 -m json.tool
API_KEY=$(echo "$KEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key') or d.get('raw_key'))")
TOKEN_HASH=$(echo "$KEY_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
echo "API Key: $API_KEY"
echo "Token hash: $TOKEN_HASH"
echo ""

echo "--- Step 4: Create second key (no RPM limit, but low RPH=5) ---"
KEY2_RESP=$(curl -s -X POST -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/keys" -d "{
  \"key_name\": \"e2e-rph-test-key\",
  \"team_id\": \"$TEAM_ID\",
  \"owner_account_id\": \"$OWNER_ACCOUNT_ID\",
  \"rpm_limit\": null,
  \"tpm_limit\": null,
  \"rph_limit\": 5,
  \"rpd_limit\": 20,
  \"tpd_limit\": null
}")
echo "$KEY2_RESP" | python3 -m json.tool
API_KEY2=$(echo "$KEY2_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key') or d.get('raw_key'))")
TOKEN_HASH2=$(echo "$KEY2_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
echo "API Key 2: $API_KEY2"
echo "Token hash 2: $TOKEN_HASH2"
echo ""

echo "--- Step 5a: Grant all models to the org ---"
curl -s -X PUT -H "$H_MASTER" -H "$H_JSON" "$BASE/ui/api/organizations/$ORG_ID/asset-access" -d '{
  "mode": "grant",
  "select_all_selectable": true
}' | python3 -c "
import sys, json
d = json.load(sys.stdin)
keys = d.get('selected_callable_keys', [])
print(f'  Granted {len(keys)} models: {keys}')
"
echo ""

echo "--- Step 5b: Verify model deployment exists ---"
curl -s -H "$H_MASTER" "$BASE/ui/api/models" | python3 -c "
import sys, json
data = json.load(sys.stdin)['data']
for m in data:
    if m['model_name'] == 'llama-3.1-8b-instant':
        print(f'Model: {m[\"model_name\"]} | Provider: {m[\"provider\"]} | ID: {m[\"deployment_id\"]}')
        break
else:
    print('ERROR: llama-3.1-8b-instant model not found!')
    sys.exit(1)
"
echo ""

echo "--- Step 6: Quick sanity check - send one chat request ---"
CHAT_RESP=$(curl -s -X POST -H "Authorization: Bearer $API_KEY" -H "$H_JSON" "$BASE/v1/chat/completions" -d '{
  "model": "llama-3.1-8b-instant",
  "messages": [{"role": "user", "content": "Say hello in exactly 3 words"}],
  "max_tokens": 20
}')
echo "$CHAT_RESP" | python3 -m json.tool 2>/dev/null || echo "$CHAT_RESP"
echo ""

echo "--- Step 7: Save test IDs to env file ---"
cat > /tmp/e2e_test_env.sh << EOF
export E2E_ORG_ID="$ORG_ID"
export E2E_TEAM_ID="$TEAM_ID"
export E2E_API_KEY="$API_KEY"
export E2E_TOKEN_HASH="$TOKEN_HASH"
export E2E_API_KEY2="$API_KEY2"
export E2E_TOKEN_HASH2="$TOKEN_HASH2"
export E2E_OWNER_ACCOUNT_ID="$OWNER_ACCOUNT_ID"
export BASE="$BASE"
export MASTER_KEY="$MASTER_KEY"
EOF
echo "Test environment saved to /tmp/e2e_test_env.sh"
echo ""
echo "============================================="
echo "  SETUP COMPLETE"
echo "============================================="
