# Public API compatibility baseline

This document records DeltaLLM's developer-facing HTTP behavior before the public DTO and OpenAPI stabilization work tracked by [GitHub issue #280](https://github.com/deltawi/deltallm/issues/280). It is an audit baseline, not a claim that every current behavior is the intended long-term contract.

Baseline revision: `9e21536e4f3e65257e182784f7b1245312eb91eb` from 2026-08-21.

The executable fixtures are under `tests/contracts/fixtures/`. Any later change to response bytes, omitted-versus-null fields, media types, error status/envelopes, route classification, JSONL rows, or webhook snapshots must update those fixtures deliberately and explain the compatibility impact.

## Developer API inventory

The machine-checked inventory is `tests/contracts/fixtures/public_route_inventory.json`. It classifies all current runtime routes by audience and gives every developer route an explicit protocol dialect.

| Method | Route | Dialect | Current purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/chat/completions` | OpenAI | Chat completions, including streaming |
| `POST` | `/v1/completions` | OpenAI | Legacy text completions |
| `POST` | `/v1/responses` | OpenAI | Responses API compatibility |
| `POST` | `/v1/embeddings` | OpenAI | Embeddings |
| `POST` | `/v1/images/generations` | OpenAI | Image generation |
| `POST` | `/v1/audio/speech` | OpenAI | Speech synthesis |
| `POST` | `/v1/audio/transcriptions` | OpenAI | Audio transcription |
| `GET` | `/v1/models` | OpenAI | Visible model list |
| `POST` | `/v1/messages` | Anthropic | Anthropic Messages compatibility |
| `POST` | `/v1/rerank` | Delta JSON | Reranking extension |
| `POST` | `/mcp` | JSON-RPC | MCP gateway |
| `POST` | `/v1/files` | OpenAI target | Upload batch JSONL |
| `GET` | `/v1/files/{file_id}` | OpenAI target | Retrieve file metadata |
| `GET` | `/v1/files/{file_id}/content` | OpenAI target | Download JSONL content |
| `POST` | `/v1/batches` | OpenAI target | Create a batch |
| `GET` | `/v1/batches` | OpenAI target | List visible batches |
| `GET` | `/v1/batches/{batch_id}` | OpenAI target | Retrieve a batch |
| `POST` | `/v1/batches/{batch_id}/cancel` | OpenAI target | Request cancellation |

The following families are not part of the developer contract planned for `/openapi/public-v1.json`:

- `/ui/api/*` administration and browser control-plane APIs;
- `/auth/*` identity/session APIs;
- `/health/*` and `/metrics` operator diagnostics;
- `/spend/*` and `/global/*` legacy reporting APIs;
- `/webhooks/email/*` provider callbacks;
- `/ui/*` browser HTML/assets; and
- the existing mixed `/openapi.json`, `/docs`, and `/redoc` operator/debugging surfaces.

## Wire formats

| Capability | Current transport | Baseline fixture |
| --- | --- | --- |
| Files/Batches objects and errors | JSON | `files_batches_http.json` |
| File upload | `multipart/form-data` file plus a currently query-bound `purpose` | `files_batches_http.json` and `batch_input.jsonl` |
| Batch input | UTF-8 JSON Lines | `batch_input.jsonl` |
| Batch success output | UTF-8 JSON Lines | `batch_output.jsonl` |
| Batch error output | UTF-8 JSON Lines | `batch_error.jsonl` |
| Downloaded batch content | `application/jsonl` at runtime | `batch_output.jsonl` |
| Terminal batch webhook | Canonical JSON bytes plus signed HTTP headers | `batch_webhook_delivery.json` |
| Gateway streams | Server-sent events (`text/event-stream`) where supported | Existing gateway/provider tests |
| MCP | JSON-RPC over HTTP | Existing MCP tests |

OpenAPI currently documents file content as an empty `application/json` response even though runtime returns `application/jsonl`. The known-gap fixture records this mismatch without changing it in the baseline PR.

## Files compatibility

| Capability | Current behavior | Intended delivery slice |
| --- | --- | --- |
| Upload | Available through `POST /v1/files` | Slice 2 moves `purpose` into multipart form data |
| Retrieve metadata | Available | Slice 1 adds a stable response DTO without changing bytes |
| Download content | Available | Slice 1 documents the correct media/body type |
| List | Not implemented | Slice 4 |
| Delete | Not implemented | Slice 4 with durable deletion |

The upload handler currently declares `purpose` as a query parameter with default `batch`. An official client succeeds for `purpose=batch` because the ignored multipart value and server default happen to agree. A conflicting query value wins. The golden test intentionally records that behavior so Slice 2 produces a visible, reviewed diff.

Current `FileObject` responses include `id`, `object`, `bytes`, `created_at`, `filename`, `purpose`, and deprecated `status`. Although expiry is persisted, `expires_at` is currently omitted from the public response.

## Batches compatibility

Supported batch endpoints:

- `/v1/embeddings`
- `/v1/chat/completions` with `stream` omitted or `false`; function tools are accepted and MCP tools are rejected

Current create fields read by the server are:

- `input_file_id`
- `endpoint`
- `completion_window`
- `metadata`
- Delta extension `webhook`

Missing, null, or blank `completion_window` values normalize to `24h`; other values are rejected. The handler currently accepts an arbitrary JSON object and silently ignores unrelated top-level fields. That permissiveness is recorded as current behavior, not promised as the stabilized request contract.

`Idempotency-Key` is read from the request. Durable idempotency is controlled by `embeddings_batch_create_idempotency_enabled`, which currently defaults to `false`.

Current batch list behavior:

- accepts only `limit`, defaulting to `20` without an explicit public maximum;
- returns `{"object":"list","data":[...]}`;
- has no object-ID `after` cursor; and
- omits `first_id`, `last_id`, and `has_more`.

Current public batch responses map internal `queued` to `validating` and an in-progress job with `cancel_requested_at` to `cancelling`. They include nullable output/error IDs and lifecycle timestamps, `errors`, request counts, and metadata. `model`, `cancelling_at`, `cancelled_at`, `finalizing_at`, and aggregate `usage` are currently omitted.

## JSONL validation

Every nonblank input line must be a JSON object with:

- unique, nonblank `custom_id`;
- `method` omitted or equal to `POST`;
- `url` omitted or exactly equal to the batch endpoint; and
- a typed request `body` for the selected endpoint.

Output and error files use the current OpenAI-compatible row wrappers shown in the fixtures. Successful rows contain `response` and `error: null`; failed/cancelled rows contain `response: null` and a typed error object. `custom_id` is preserved in both.

## Webhook compatibility and safety

The create-only webhook object contains `url` and `signing_secret`. Only `{"configured":true}` is returned in Batch objects. The request secret is encrypted before persistence and does not appear in HTTP responses, webhook snapshots, validation diagnostics, logs, reprs, or checked-in examples.

Webhook delivery is at least once. Retries preserve the event ID and exact canonical body. Every attempt changes its timestamp, signature, and attempt header. Receivers must verify the HMAC over the raw request bytes before parsing JSON, enforce a timestamp tolerance, and deduplicate by event ID.

## Errors and tenant visibility

Current Files/Batches errors use FastAPI's `{"detail":...}` response shape:

- missing resources return 404;
- foreign resources currently return 403 and therefore reveal that an ID exists;
- request binding failures return 422 `HTTPValidationError`; and
- service/runtime validation generally returns an HTTP status with a string or structured `detail`.

Slice 2 deliberately changes public REST errors to stable OpenAI/Delta envelopes, converts public request validation to HTTP 400, and makes foreign resources indistinguishable from missing resources. Anthropic Messages and MCP JSON-RPC retain their own dialects.

## Limits: defaults versus production example

The values near the top of `docs/features/batching.md` are an example production profile, not defaults. The authoritative defaults are the typed settings in `src/config.py` and the constructor defaults used by `BatchService`.

| Setting | Code default | Example production profile | Meaning |
| --- | ---: | ---: | --- |
| `embeddings_batch_max_file_bytes` | 52,428,800 bytes (50 MiB) | 209,715,200 bytes (200 MiB) | Maximum uploaded batch file bytes |
| `embeddings_batch_max_items_per_batch` | 10,000 | 50,000 | Maximum nonblank validated JSONL items |
| `embeddings_batch_max_line_bytes` | 1,048,576 bytes (1 MiB) | Inherits default in the example | Maximum single JSONL line bytes |
| `embeddings_batch_max_pending_batches_per_scope` | 20 | 50 | Maximum active batches for the effective scope; `0` disables the cap |
| `embeddings_batch_create_idempotency_enabled` | `false` | Not enabled in the example | Enables durable create resolution by scoped idempotency key |

Deployments may choose lower or higher bounded values. Client code must treat limits as deployment configuration rather than universal constants.

## Official client smoke baseline

The baseline uses public operations documented by the official [OpenAI Files API](https://developers.openai.com/api/reference/resources/files) and [OpenAI Batches API](https://developers.openai.com/api/reference/resources/batches).

| Client | Locked version | Passing smoke coverage | Named known gaps |
| --- | --- | --- | --- |
| OpenAI Python | `3.3.1` in `uv.lock` | upload, file retrieve/content, batch create/retrieve/list/cancel, metadata, `extra_headers`, and Delta `extra_body` | Files list; correct multi-page batch cursor traversal |
| OpenAI TypeScript | `7.5.0` in `package-lock.json` | multipart upload serialization, file retrieve/content, batch create/retrieve/list/cancel, metadata, and request headers | Files list; correct multi-page batch cursor traversal |

Run the focused baseline with:

```bash
uv run pytest -v -rs tests/contracts tests/compat/openai_python
npm run test:compat
```

Each expected failure names exactly one missing capability and its delivery slice. An unexpected pass is strict in Python so the test must be updated when a gap closes. TypeScript uses named `todo` entries until the real HTTP conformance server arrives in Slice 6.

## Current OpenAPI gaps

`tests/contracts/fixtures/openapi_known_gaps.json` is the executable list. At baseline:

- the full schema mixes developer, admin, auth, health, metrics, and reporting routes;
- protected routes expose an optional raw `Authorization` header parameter and no `BearerAuth` scheme;
- Files/Batches successful response schemas are empty;
- batch create is an arbitrary object;
- file `purpose` is query-bound instead of multipart;
- file content has the wrong documented media type;
- Files list/delete and Batch cursor metadata are missing; and
- public validation advertises the FastAPI 422 envelope.

Later slices should remove a known-gap entry only in the same change that adds the intended contract, wire tests, official-client coverage, and compatibility notes.
