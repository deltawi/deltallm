#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:4100}"
API_KEY="${API_KEY:-}"
MODEL="${MODEL:-}"
BATCH_POLL_INTERVAL="${BATCH_POLL_INTERVAL:-2}"
BATCH_TIMEOUT_SECONDS="${BATCH_TIMEOUT_SECONDS:-120}"
BATCH_ITEMS="${BATCH_ITEMS:-3}"
WORKDIR="${WORKDIR:-/tmp/deltallm-e2e-embeddings}"

if [[ -z "${API_KEY}" || -z "${MODEL}" ]]; then
  echo "Usage: API_KEY=... MODEL=... [BASE_URL=http://localhost:4100] $0" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python interpreter not found: ${PYTHON_BIN}" >&2
  exit 1
fi

mkdir -p "${WORKDIR}"
SINGLE_RES="${WORKDIR}/single_response.json"
BATCH_INPUT="${WORKDIR}/batch_input.jsonl"
FILE_RES="${WORKDIR}/file_response.json"
BATCH_CREATE_RES="${WORKDIR}/batch_create_response.json"
BATCH_STATUS_RES="${WORKDIR}/batch_status_response.json"
OUTPUT_FILE_RES="${WORKDIR}/batch_output_file.json"
ERROR_FILE_RES="${WORKDIR}/batch_error_file.json"
OUTPUT_CONTENT="${WORKDIR}/batch_output_content.jsonl"
ERROR_CONTENT="${WORKDIR}/batch_error_content.jsonl"

auth_header="Authorization: Bearer ${API_KEY}"

echo "[1/2] Single embedding request"
curl -sS "${BASE_URL}/v1/embeddings" \
  -H "${auth_header}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"input\":\"single embedding e2e probe\"}" \
  > "${SINGLE_RES}"

"${PYTHON_BIN}" - "${SINGLE_RES}" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
if "error" in data:
    raise SystemExit(f"Single embedding failed: {data['error']}")
items = data.get("data")
if not isinstance(items, list) or len(items) == 0:
    raise SystemExit("Single embedding response has no data entries")
emb = items[0].get("embedding")
if not isinstance(emb, list) or len(emb) == 0:
    raise SystemExit("Single embedding vector missing/empty")
print(f"Single embedding ok: dim={len(emb)}")
PY

echo "[2/2] Batch embedding request"
cat > "${BATCH_INPUT}" <<EOF
EOF
for i in $(seq 1 "${BATCH_ITEMS}"); do
  echo "{\"custom_id\":\"item-${i}\",\"method\":\"POST\",\"url\":\"/v1/embeddings\",\"body\":{\"model\":\"${MODEL}\",\"input\":\"batch probe ${i}\"}}" >> "${BATCH_INPUT}"
done

curl -sS "${BASE_URL}/v1/files" \
  -H "${auth_header}" \
  -F "purpose=batch" \
  -F "file=@${BATCH_INPUT};type=application/jsonl" \
  > "${FILE_RES}"

INPUT_FILE_ID="$("${PYTHON_BIN}" - "${FILE_RES}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
if "error" in data:
    raise SystemExit(f"File upload failed: {data['error']}")
print(data.get("id",""))
PY
)"

if [[ -z "${INPUT_FILE_ID}" ]]; then
  echo "File upload did not return file id" >&2
  exit 1
fi

curl -sS "${BASE_URL}/v1/batches" \
  -H "${auth_header}" \
  -H "Content-Type: application/json" \
  -d "{\"input_file_id\":\"${INPUT_FILE_ID}\",\"endpoint\":\"/v1/embeddings\",\"completion_window\":\"24h\"}" \
  > "${BATCH_CREATE_RES}"

BATCH_ID="$("${PYTHON_BIN}" - "${BATCH_CREATE_RES}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
if "error" in data:
    raise SystemExit(f"Batch create failed: {data['error']}")
print(data.get("id",""))
PY
)"

if [[ -z "${BATCH_ID}" ]]; then
  echo "Batch creation did not return batch id" >&2
  exit 1
fi

start_epoch="$(date +%s)"
final_status=""
while true; do
  curl -sS "${BASE_URL}/v1/batches/${BATCH_ID}" \
    -H "${auth_header}" \
    > "${BATCH_STATUS_RES}"
  final_status="$("${PYTHON_BIN}" - "${BATCH_STATUS_RES}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
if "error" in data:
    raise SystemExit(f"Batch status failed: {data['error']}")
print(data.get("status",""))
PY
)"
  echo "Batch status: ${final_status}"
  if [[ "${final_status}" == "completed" || "${final_status}" == "failed" || "${final_status}" == "cancelled" || "${final_status}" == "expired" ]]; then
    break
  fi
  now_epoch="$(date +%s)"
  elapsed="$((now_epoch - start_epoch))"
  if (( elapsed >= BATCH_TIMEOUT_SECONDS )); then
    echo "Timed out waiting for batch completion (${BATCH_TIMEOUT_SECONDS}s)" >&2
    exit 1
  fi
  sleep "${BATCH_POLL_INTERVAL}"
done

"${PYTHON_BIN}" - "${BATCH_STATUS_RES}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
counts = (data.get("request_counts") or {})
print("Batch counts:", counts)
PY

OUTPUT_FILE_ID="$("${PYTHON_BIN}" - "${BATCH_STATUS_RES}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print(data.get("output_file_id") or "")
PY
)"
ERROR_FILE_ID="$("${PYTHON_BIN}" - "${BATCH_STATUS_RES}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print(data.get("error_file_id") or "")
PY
)"

if [[ -n "${OUTPUT_FILE_ID}" ]]; then
  curl -sS "${BASE_URL}/v1/files/${OUTPUT_FILE_ID}" -H "${auth_header}" > "${OUTPUT_FILE_RES}"
  curl -sS "${BASE_URL}/v1/files/${OUTPUT_FILE_ID}/content" -H "${auth_header}" > "${OUTPUT_CONTENT}"
  echo "Batch output file: ${OUTPUT_CONTENT}"
fi

if [[ -n "${ERROR_FILE_ID}" ]]; then
  curl -sS "${BASE_URL}/v1/files/${ERROR_FILE_ID}" -H "${auth_header}" > "${ERROR_FILE_RES}"
  curl -sS "${BASE_URL}/v1/files/${ERROR_FILE_ID}/content" -H "${auth_header}" > "${ERROR_CONTENT}"
  echo "Batch error file: ${ERROR_CONTENT}"
fi

if [[ "${final_status}" != "completed" ]]; then
  echo "Batch finished with status=${final_status}" >&2
  exit 1
fi

echo "E2E embeddings checks passed."
