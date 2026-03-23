# gRPC Transport

DeltaLLM can talk to **vLLM** and **NVIDIA Triton Inference Server** over gRPC instead of HTTP.
gRPC typically reduces per-request overhead and gives better streaming throughput for high-volume inference workloads.

## Quick Path

### vLLM over gRPC

```yaml
model_list:
  - model_name: llama-3-8b
    deployment_id: vllm-grpc
    deltallm_params:
      model: vllm/meta-llama/Llama-3-8b
      transport: grpc
      grpc_address: "vllm-host:50051"
      http_fallback_base: "http://vllm-host:8000/v1"   # optional
```

### Triton over gRPC

```yaml
model_list:
  - model_name: triton-ensemble
    deployment_id: triton-grpc
    deltallm_params:
      model: triton/ensemble_llm
      transport: grpc
      grpc_address: "triton-host:8001"
      triton_model_name: ensemble_llm
      triton_model_version: "1"                          # optional, defaults to latest
```

Verify with a normal chat request — the gateway handles the protocol translation:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -d '{
    "model": "llama-3-8b",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

## How It Works

```
Client  ─── OpenAI-compatible HTTP ───▶  DeltaLLM Gateway
                                              │
                                    ┌─────────┴─────────┐
                                    │ transport="http"   │ transport="grpc"
                                    ▼                    ▼
                              HTTP/REST            gRPC channel
                              to provider          to vLLM / Triton
```

1. The gateway receives a standard OpenAI-compatible request.
2. It resolves the deployment and checks the `transport` field.
3. For `grpc`, the request is translated by the appropriate adapter (vLLM or Triton) and sent over a pooled gRPC channel.
4. The response is translated back into the standard OpenAI format before returning to the client.

Streaming works the same way — server-sent events are generated from the gRPC stream.

## Configuration Reference

These fields go inside `deltallm_params`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `transport` | `"http"` \| `"grpc"` | `"http"` | Protocol to use when calling the provider |
| `api_base` | string | — | Set to `grpc://host:port` as a shorthand for gRPC transport (auto-sets transport + grpc_address) |
| `grpc_address` | string | — | Host and port for the gRPC endpoint (e.g. `"localhost:50051"`) |
| `http_fallback_base` | string | — | Base URL used if the gateway needs to fall back to HTTP on retryable gRPC errors |
| `triton_model_name` | string | — | Triton model repository name (Triton only) |
| `triton_model_version` | string | `""` (latest) | Triton model version (Triton only) |

## gRPC Channel Manager

DeltaLLM maintains a connection pool so channels are reused across requests:

- Channels are created on first use and cached by address.
- Keepalive pings are sent every 30 seconds to detect dead connections.
- The pool has a configurable maximum size (default 16). When full, the least-recently-used channel is closed.
- All channels are closed gracefully on shutdown.

## Provider Details

### vLLM

vLLM exposes an OpenAI-compatible gRPC service at port **50051** (by default when started with `--enable-grpc`).
The adapter sends chat messages as a JSON payload over gRPC and parses the response as a standard completion object.

**Supported operations:** chat completions (non-streaming and streaming).

### NVIDIA Triton Inference Server

Triton uses a protobuf-based gRPC protocol on port **8001** (default).
The adapter constructs protobuf-encoded `ModelInferRequest` messages with the prompt serialized as a `text_input` BYTES tensor, and parses the `text_output` tensor from the protobuf response.

Messages are concatenated into a prompt using a chat-template style format:

```
<|system|> You are a helpful assistant.
<|user|> What is 2+2?
```

**Supported operations:** chat completions (non-streaming and streaming).

**Required fields:** `triton_model_name` must match the model name in your Triton model repository.

## Admin UI

When creating or editing a model deployment for a gRPC-capable provider (vLLM or Triton), the admin UI shows additional fields:

- **Transport** — toggle between HTTP and gRPC
- **gRPC Address** — the `host:port` of the gRPC endpoint
- **HTTP Fallback URL** — optional fallback for when gRPC is unavailable
- **Triton Model Name / Version** — shown only when the provider is Triton

The Models list shows a transport badge (HTTP or gRPC) next to each deployment, and the Model Detail page displays the full gRPC configuration in the overview tab.

## Failover

When `http_fallback_base` (or `api_base`) is set on a gRPC deployment, the gateway will automatically retry failed gRPC calls over HTTP for retryable errors:

- **UNAVAILABLE** — the gRPC server is down or unreachable
- **DEADLINE_EXCEEDED** — the gRPC call timed out

This is useful during rolling deployments where the gRPC port may be temporarily unavailable while the HTTP endpoint is still serving.

The fallback path uses the standard OpenAI-compatible HTTP adapter, so the upstream must also expose an OpenAI-compatible HTTP API at the fallback URL.

## Soft Dependency

The `grpcio` package is an optional dependency.
If it is not installed, models configured with `transport: "grpc"` will return a clear error at request time explaining that gRPC support requires `pip install grpcio`.
All HTTP-based functionality continues to work normally.
