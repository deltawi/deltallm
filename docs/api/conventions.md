# API Conventions

This page describes behavior shared by DeltaLLM's HTTP surfaces. The generated
[OpenAPI schema](openapi.md) remains the field-level source for operations that declare typed
request and response models.

## API surfaces

| Surface | Typical caller | Compatibility contract |
| --- | --- | --- |
| `/v1/*` | Applications and OpenAI-compatible SDKs | Preserves the documented OpenAI-compatible request, response, error, and streaming behavior |
| `/mcp` and MCP tooling routes | MCP clients and MCP-aware applications | MCP protocol plus DeltaLLM governance extensions |
| `/ui/api/*` and `/auth/*` | Admin UI and operator integrations | DeltaLLM control-plane contract; not an OpenAI-compatible surface |
| `/health/*` and `/metrics` | Probes and monitoring systems | Operational contract with deployment-specific exposure requirements |

## Authentication

Proxy calls use a bearer token in the configured key header, normally:

```http
Authorization: Bearer <token>
```

The token can be the master key, a virtual API key, or—when enabled—a JWT/custom credential.
Admin UI calls normally use the `deltallm_session` cookie. Some bootstrap and administrative
operations also accept master-key authorization as documented for that endpoint.

Authentication does not replace tenant authorization. Organization, team, user, key, model,
and permission scope are resolved server-side for every protected operation.

## Content type and JSON

JSON endpoints use `Content-Type: application/json`. File and audio uploads use the media type
declared by the operation schema, usually `multipart/form-data`. Unknown fields are handled by
the endpoint's Pydantic model or explicit compatibility adapter; do not assume all surfaces
silently forward arbitrary fields.

## Errors

Gateway/proxy errors use the OpenAI-style envelope:

```json
{
  "error": {
    "message": "Model not found",
    "type": "model_not_found",
    "param": null,
    "code": null
  }
}
```

Validation and some control-plane errors use FastAPI's `detail` envelope. Clients integrating
with both proxy and Admin APIs must handle both documented forms. Error messages are sanitized;
they do not expose provider credentials or raw upstream bodies.

Common statuses include:

| Status | Meaning |
| ---: | --- |
| `400` | Invalid request or unsupported compatibility behavior |
| `401` | Missing, invalid, revoked, expired, or inactive-tenant credential |
| `403` | Authenticated but outside the required scope or permission |
| `404` | Resource/model not found or intentionally non-enumerated |
| `408` | Gateway deadline exceeded |
| `409` | State conflict or an approval workflow is required |
| `422` | Typed request validation failed |
| `429` | Rate, concurrency, or budget admission rejected the request |
| `503` | Required gateway dependency/capacity or an eligible deployment is unavailable |

A `429` response can include `Retry-After` when the gateway has a bounded retry interval.

## Pagination

There is not yet one universal pagination contract across all Admin APIs. Many list operations
use `limit` and `offset` and return a `pagination` object containing `total`, `limit`, `offset`,
and `has_more`; other operations use endpoint-specific bounds. Follow each operation's OpenAPI
parameters rather than assuming a cursor or unlimited list.

## Idempotency

Idempotency is endpoint-specific. `POST /v1/batches` accepts `Idempotency-Key` when batch-create
idempotency is enabled. Reusing the key within the same authenticated scope protects that batch
creation workflow. Do not send the header to an arbitrary mutation and assume it will be
deduplicated.

Retries remain the client's responsibility unless an endpoint explicitly documents a stable
idempotency key. Never retry a non-idempotent mutation automatically after an ambiguous timeout.

## Correlation IDs

Clients can provide a bounded `X-Request-ID`; DeltaLLM uses it for supported audit, request-log,
and correlation fields. The current API does not guarantee that an arbitrary client value is
returned as a response header or treated as an idempotency key.

## Streaming

OpenAI-compatible streaming endpoints use Server-Sent Events. Successful chat-compatible
streams emit `data:` frames and finish with the documented `[DONE]` sentinel. A stream may fail
over only before DeltaLLM has sent the first validated downstream chunk. After response bytes
are sent, the gateway terminates on failure instead of silently replacing the stream with a
different provider response.

Clients must handle cancellation, truncated streams, and timeouts. A successful HTTP status and
initial frame do not guarantee that a long stream will finish.
