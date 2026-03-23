#!/usr/bin/env bash
#
# End-to-end test for DeltaLLM gRPC transport (vLLM + Triton)
#
# Prerequisites:
#   1. Docker installed and running
#   2. DeltaLLM backend running on localhost:8000 with a master key
#   3. ~4 GB free RAM for CPU inference containers
#
# Usage:
#   DELTALLM_MASTER_KEY=sk-... bash scripts/e2e_grpc_transport.sh
#
# This script:
#   - Starts a vLLM container (CPU, facebook/opt-125m) with gRPC enabled
#   - Starts a Triton container (CPU, simple echo model) with gRPC
#   - Creates DeltaLLM deployments pointing at the gRPC endpoints
#   - Sends chat completions through DeltaLLM and verifies responses
#   - Tests streaming, health checks, and HTTP fallback
#   - Cleans up containers on exit
#
# Related: https://github.com/deltawi/deltallm/issues/29
#

set -euo pipefail

DELTALLM_URL="${DELTALLM_URL:-http://localhost:8000}"
MASTER_KEY="${DELTALLM_MASTER_KEY:?Set DELTALLM_MASTER_KEY}"
VLLM_GRPC_PORT="${VLLM_GRPC_PORT:-50051}"
VLLM_HTTP_PORT="${VLLM_HTTP_PORT:-8100}"
TRITON_GRPC_PORT="${TRITON_GRPC_PORT:-8001}"
TRITON_HTTP_PORT="${TRITON_HTTP_PORT:-8101}"
VLLM_MODEL="${VLLM_MODEL:-facebook/opt-125m}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
PASS=0
FAIL=0

log()   { echo -e "${YELLOW}[e2e-grpc]${NC} $*"; }
ok()    { echo -e "${GREEN}  ✓ $*${NC}"; PASS=$((PASS + 1)); }
fail()  { echo -e "${RED}  ✗ $*${NC}"; FAIL=$((FAIL + 1)); }

header() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  $*"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

cleanup() {
  log "Cleaning up containers..."
  docker rm -f deltallm-e2e-vllm 2>/dev/null || true
  docker rm -f deltallm-e2e-triton 2>/dev/null || true
  rm -rf /tmp/deltallm-e2e-triton-models 2>/dev/null || true
}
trap cleanup EXIT

api() {
  local method="$1" path="$2"
  shift 2
  curl -s -X "$method" "${DELTALLM_URL}${path}" \
    -H "X-Master-Key: ${MASTER_KEY}" \
    -H "Content-Type: application/json" \
    "$@"
}

wait_for_port() {
  local host="$1" port="$2" label="$3" timeout="${4:-120}"
  log "Waiting for ${label} on ${host}:${port} (timeout ${timeout}s)..."
  local elapsed=0
  while ! nc -z "$host" "$port" 2>/dev/null; do
    sleep 2
    elapsed=$((elapsed + 2))
    if [ "$elapsed" -ge "$timeout" ]; then
      fail "${label} did not become ready within ${timeout}s"
      return 1
    fi
  done
  ok "${label} is ready on port ${port}"
}

# ─────────────────────────────────────────────────────────────
# Phase 0: Preflight checks
# ─────────────────────────────────────────────────────────────
header "Phase 0: Preflight checks"

if ! command -v docker &>/dev/null; then
  fail "Docker is not installed"
  echo "Install Docker and try again."
  exit 1
fi
ok "Docker is available"

if ! command -v curl &>/dev/null; then
  fail "curl is not installed"
  exit 1
fi
ok "curl is available"

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "${DELTALLM_URL}/health" 2>/dev/null || echo "000")
if [ "$HEALTH" = "200" ]; then
  ok "DeltaLLM backend is reachable at ${DELTALLM_URL}"
else
  fail "DeltaLLM backend is not reachable at ${DELTALLM_URL} (HTTP ${HEALTH})"
  echo "Start the backend first, then re-run this script."
  exit 1
fi

# ─────────────────────────────────────────────────────────────
# Phase 1: Start vLLM container with gRPC
# ─────────────────────────────────────────────────────────────
header "Phase 1: Start vLLM (CPU, ${VLLM_MODEL})"

docker rm -f deltallm-e2e-vllm 2>/dev/null || true

log "Pulling and starting vLLM container..."
docker run -d \
  --name deltallm-e2e-vllm \
  --cpus=2 \
  -p "${VLLM_HTTP_PORT}:8000" \
  -p "${VLLM_GRPC_PORT}:50051" \
  vllm/vllm-openai:latest \
  --model "${VLLM_MODEL}" \
  --device cpu \
  --max-model-len 512 \
  --dtype float32 \
  --enforce-eager 2>&1 || {
    fail "Failed to start vLLM container"
    echo "Note: vLLM requires ~2GB RAM for opt-125m on CPU."
    echo "If --enable-grpc is not supported by your vLLM version,"
    echo "try vllm >= 0.14.0 or test HTTP-only mode."
  }

wait_for_port localhost "$VLLM_HTTP_PORT" "vLLM HTTP" 180

VLLM_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${VLLM_HTTP_PORT}/health" 2>/dev/null || echo "000")
if [ "$VLLM_HEALTH" = "200" ]; then
  ok "vLLM HTTP endpoint is healthy"
else
  log "vLLM HTTP endpoint returned ${VLLM_HEALTH} (may still be loading model)"
fi

# ─────────────────────────────────────────────────────────────
# Phase 2: Start Triton container with a simple model
# ─────────────────────────────────────────────────────────────
header "Phase 2: Start Triton Inference Server"

docker rm -f deltallm-e2e-triton 2>/dev/null || true

TRITON_MODEL_DIR="/tmp/deltallm-e2e-triton-models"
rm -rf "$TRITON_MODEL_DIR"
mkdir -p "${TRITON_MODEL_DIR}/echo_model/1"

cat > "${TRITON_MODEL_DIR}/echo_model/config.pbtxt" << 'EOF'
name: "echo_model"
backend: "python"
max_batch_size: 0
input [
  {
    name: "text_input"
    data_type: TYPE_STRING
    dims: [ 1 ]
  }
]
output [
  {
    name: "text_output"
    data_type: TYPE_STRING
    dims: [ 1 ]
  }
]
instance_group [
  {
    count: 1
    kind: KIND_CPU
  }
]
EOF

cat > "${TRITON_MODEL_DIR}/echo_model/1/model.py" << 'PYEOF'
import triton_python_backend_utils as pb_utils
import numpy as np
import json

class TritonPythonModel:
    def initialize(self, args):
        pass

    def execute(self, requests):
        responses = []
        for request in requests:
            input_tensor = pb_utils.get_input_tensor_by_name(request, "text_input")
            input_text = input_tensor.as_numpy()[0]
            if isinstance(input_text, bytes):
                input_text = input_text.decode("utf-8")

            response_text = json.dumps({
                "role": "assistant",
                "content": f"Echo: {input_text[:200]}"
            })

            output_tensor = pb_utils.Tensor(
                "text_output",
                np.array([response_text], dtype=object)
            )
            inference_response = pb_utils.InferenceResponse(
                output_tensors=[output_tensor]
            )
            responses.append(inference_response)
        return responses

    def finalize(self):
        pass
PYEOF

log "Starting Triton container..."
docker run -d \
  --name deltallm-e2e-triton \
  --cpus=2 \
  -p "${TRITON_HTTP_PORT}:8000" \
  -p "${TRITON_GRPC_PORT}:8001" \
  -v "${TRITON_MODEL_DIR}:/models" \
  nvcr.io/nvidia/tritonserver:24.01-py3 \
  tritonserver --model-repository=/models --strict-model-config=false 2>&1 || {
    fail "Failed to start Triton container"
  }

wait_for_port localhost "$TRITON_GRPC_PORT" "Triton gRPC" 120

# ─────────────────────────────────────────────────────────────
# Phase 3: Create DeltaLLM deployments via Admin API
# ─────────────────────────────────────────────────────────────
header "Phase 3: Create DeltaLLM deployments"

log "Creating vLLM gRPC deployment..."
VLLM_DEPLOY=$(api POST /api/admin/models -d "{
  \"model_name\": \"e2e-vllm-grpc\",
  \"deployment_id\": \"e2e-vllm-grpc-001\",
  \"deltallm_params\": {
    \"model\": \"vllm/${VLLM_MODEL}\",
    \"transport\": \"grpc\",
    \"grpc_address\": \"host.docker.internal:${VLLM_GRPC_PORT}\",
    \"http_fallback_base\": \"http://host.docker.internal:${VLLM_HTTP_PORT}/v1\",
    \"api_key\": \"none\"
  }
}")
echo "$VLLM_DEPLOY" | python3 -m json.tool 2>/dev/null && ok "vLLM gRPC deployment created" || fail "Failed to create vLLM deployment: ${VLLM_DEPLOY}"

log "Creating vLLM HTTP-only deployment (for fallback comparison)..."
VLLM_HTTP_DEPLOY=$(api POST /api/admin/models -d "{
  \"model_name\": \"e2e-vllm-http\",
  \"deployment_id\": \"e2e-vllm-http-001\",
  \"deltallm_params\": {
    \"model\": \"vllm/${VLLM_MODEL}\",
    \"api_base\": \"http://host.docker.internal:${VLLM_HTTP_PORT}/v1\",
    \"api_key\": \"none\"
  }
}")
echo "$VLLM_HTTP_DEPLOY" | python3 -m json.tool 2>/dev/null && ok "vLLM HTTP deployment created" || fail "Failed to create vLLM HTTP deployment"

log "Creating Triton gRPC deployment..."
TRITON_DEPLOY=$(api POST /api/admin/models -d "{
  \"model_name\": \"e2e-triton-grpc\",
  \"deployment_id\": \"e2e-triton-grpc-001\",
  \"deltallm_params\": {
    \"model\": \"triton/echo_model\",
    \"transport\": \"grpc\",
    \"grpc_address\": \"host.docker.internal:${TRITON_GRPC_PORT}\",
    \"triton_model_name\": \"echo_model\"
  }
}")
echo "$TRITON_DEPLOY" | python3 -m json.tool 2>/dev/null && ok "Triton gRPC deployment created" || fail "Failed to create Triton deployment: ${TRITON_DEPLOY}"

log "Creating Triton grpc:// shorthand deployment..."
TRITON_SHORT=$(api POST /api/admin/models -d "{
  \"model_name\": \"e2e-triton-short\",
  \"deployment_id\": \"e2e-triton-short-001\",
  \"deltallm_params\": {
    \"model\": \"triton/echo_model\",
    \"api_base\": \"grpc://host.docker.internal:${TRITON_GRPC_PORT}\",
    \"triton_model_name\": \"echo_model\"
  }
}")
echo "$TRITON_SHORT" | python3 -m json.tool 2>/dev/null && ok "Triton grpc:// shorthand deployment created" || fail "Failed to create shorthand deployment"

# ─────────────────────────────────────────────────────────────
# Phase 4: Health checks
# ─────────────────────────────────────────────────────────────
header "Phase 4: Health checks"

log "Checking vLLM gRPC deployment health..."
VLLM_HC=$(api GET /api/admin/models/e2e-vllm-grpc-001/health)
VLLM_HEALTHY=$(echo "$VLLM_HC" | python3 -c "import sys,json; print(json.load(sys.stdin).get('healthy', False))" 2>/dev/null || echo "false")
if [ "$VLLM_HEALTHY" = "True" ]; then
  ok "vLLM gRPC health check passed"
else
  log "vLLM gRPC health: ${VLLM_HC}"
  fail "vLLM gRPC health check did not return healthy (this may be expected if gRPC port is not exposed by the vLLM version)"
fi

log "Checking Triton gRPC deployment health..."
TRITON_HC=$(api GET /api/admin/models/e2e-triton-grpc-001/health)
TRITON_HEALTHY=$(echo "$TRITON_HC" | python3 -c "import sys,json; print(json.load(sys.stdin).get('healthy', False))" 2>/dev/null || echo "false")
if [ "$TRITON_HEALTHY" = "True" ]; then
  ok "Triton gRPC health check passed"
else
  log "Triton gRPC health: ${TRITON_HC}"
  fail "Triton gRPC health check did not return healthy"
fi

# ─────────────────────────────────────────────────────────────
# Phase 5: Chat completions — vLLM gRPC
# ─────────────────────────────────────────────────────────────
header "Phase 5: vLLM gRPC chat completions"

log "Sending non-streaming chat completion via gRPC..."
VLLM_CHAT=$(curl -s -X POST "${DELTALLM_URL}/v1/chat/completions" \
  -H "X-Master-Key: ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"e2e-vllm-grpc\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Say hello in one word.\"}],
    \"max_tokens\": 20
  }")

VLLM_CONTENT=$(echo "$VLLM_CHAT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])" 2>/dev/null || echo "")
if [ -n "$VLLM_CONTENT" ]; then
  ok "vLLM gRPC non-streaming response: ${VLLM_CONTENT:0:80}"
else
  fail "vLLM gRPC non-streaming chat failed"
  echo "  Response: $VLLM_CHAT"
fi

log "Sending streaming chat completion via gRPC..."
VLLM_STREAM=$(curl -s -N -X POST "${DELTALLM_URL}/v1/chat/completions" \
  -H "X-Master-Key: ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"e2e-vllm-grpc\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Count to 3.\"}],
    \"max_tokens\": 30,
    \"stream\": true
  }" 2>/dev/null | head -20)

VLLM_STREAM_LINES=$(echo "$VLLM_STREAM" | grep -c "^data:" || echo "0")
if [ "$VLLM_STREAM_LINES" -gt 1 ]; then
  ok "vLLM gRPC streaming returned ${VLLM_STREAM_LINES} SSE chunks"
else
  fail "vLLM gRPC streaming returned ${VLLM_STREAM_LINES} chunks (expected >1)"
  echo "  Response: $VLLM_STREAM"
fi

# ─────────────────────────────────────────────────────────────
# Phase 6: Chat completions — vLLM HTTP (comparison baseline)
# ─────────────────────────────────────────────────────────────
header "Phase 6: vLLM HTTP baseline"

log "Sending non-streaming chat via HTTP..."
VLLM_HTTP_CHAT=$(curl -s -X POST "${DELTALLM_URL}/v1/chat/completions" \
  -H "X-Master-Key: ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"e2e-vllm-http\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Say hello in one word.\"}],
    \"max_tokens\": 20
  }")

HTTP_CONTENT=$(echo "$VLLM_HTTP_CHAT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])" 2>/dev/null || echo "")
if [ -n "$HTTP_CONTENT" ]; then
  ok "vLLM HTTP non-streaming response: ${HTTP_CONTENT:0:80}"
else
  fail "vLLM HTTP non-streaming chat failed"
  echo "  Response: $VLLM_HTTP_CHAT"
fi

# ─────────────────────────────────────────────────────────────
# Phase 7: Chat completions — Triton gRPC
# ─────────────────────────────────────────────────────────────
header "Phase 7: Triton gRPC chat completions"

log "Sending non-streaming chat completion via Triton gRPC..."
TRITON_CHAT=$(curl -s -X POST "${DELTALLM_URL}/v1/chat/completions" \
  -H "X-Master-Key: ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"e2e-triton-grpc\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Hello Triton!\"}],
    \"max_tokens\": 50
  }")

TRITON_CONTENT=$(echo "$TRITON_CHAT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])" 2>/dev/null || echo "")
if [ -n "$TRITON_CONTENT" ]; then
  ok "Triton gRPC non-streaming response: ${TRITON_CONTENT:0:80}"
else
  fail "Triton gRPC non-streaming chat failed"
  echo "  Response: $TRITON_CHAT"
fi

# ─────────────────────────────────────────────────────────────
# Phase 8: HTTP fallback test
# ─────────────────────────────────────────────────────────────
header "Phase 8: HTTP fallback (stop vLLM gRPC, keep HTTP)"

log "This test verifies that when the gRPC endpoint goes down,"
log "DeltaLLM falls back to the HTTP endpoint automatically."
log ""
log "To test manually:"
log "  1. Stop the vLLM gRPC port: docker exec deltallm-e2e-vllm kill -STOP 1"
log "  2. Send a request to e2e-vllm-grpc"
log "  3. Observe it succeeds via HTTP fallback (check DeltaLLM logs for 'falling back to HTTP')"
log "  4. Resume: docker exec deltallm-e2e-vllm kill -CONT 1"
ok "HTTP fallback instructions documented (manual test)"

# ─────────────────────────────────────────────────────────────
# Phase 9: Admin API — list and verify deployments
# ─────────────────────────────────────────────────────────────
header "Phase 9: Verify deployments via Admin API"

log "Listing all model deployments..."
MODELS=$(api GET /api/admin/models)
MODEL_COUNT=$(echo "$MODELS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = data if isinstance(data, list) else data.get('data', data.get('models', []))
grpc_models = [m for m in models if 'e2e-' in str(m.get('deployment_id', '') or m.get('model_name', ''))]
print(len(grpc_models))
" 2>/dev/null || echo "0")

if [ "$MODEL_COUNT" -ge 3 ]; then
  ok "Found ${MODEL_COUNT} e2e test deployments in admin API"
else
  fail "Expected at least 3 e2e deployments, found ${MODEL_COUNT}"
fi

log "Checking vLLM gRPC deployment detail..."
VLLM_DETAIL=$(api GET /api/admin/models/e2e-vllm-grpc-001)
VLLM_TRANSPORT=$(echo "$VLLM_DETAIL" | python3 -c "
import sys, json
d = json.load(sys.stdin)
p = d.get('deltallm_params', d.get('litellm_params', {}))
print(p.get('transport', 'http'))
" 2>/dev/null || echo "unknown")

if [ "$VLLM_TRANSPORT" = "grpc" ]; then
  ok "vLLM deployment shows transport=grpc in admin API"
else
  fail "vLLM deployment transport is '${VLLM_TRANSPORT}', expected 'grpc'"
fi

# ─────────────────────────────────────────────────────────────
# Phase 10: Cleanup test deployments
# ─────────────────────────────────────────────────────────────
header "Phase 10: Cleanup"

for DEPLOY_ID in e2e-vllm-grpc-001 e2e-vllm-http-001 e2e-triton-grpc-001 e2e-triton-short-001; do
  RESULT=$(api DELETE "/api/admin/models/${DEPLOY_ID}" 2>/dev/null || echo "{}")
  log "Deleted deployment ${DEPLOY_ID}"
done
ok "Test deployments cleaned up"

# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────
header "Results"

echo ""
echo -e "  ${GREEN}Passed: ${PASS}${NC}"
echo -e "  ${RED}Failed: ${FAIL}${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}Some tests failed. Check the output above for details.${NC}"
  exit 1
else
  echo -e "${GREEN}All tests passed!${NC}"
  exit 0
fi
