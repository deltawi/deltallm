# Public API contract and companion SDK implementation plan

> Status: implementation in progress; Slice 0 establishes the executable compatibility baseline without changing runtime behavior.
>
> Decision: stabilize and publish DeltaLLM's public API contract first, then add a thin Python companion SDK in this repository. Do not create a separate SDK repository yet.

## 1. Planning baseline

| Item | Value |
| --- | --- |
| Worktree | `.worktrees/sdk-public-contract` |
| Branch | `feat/public-contract-baseline` |
| Remote base | `origin/main` |
| Base commit | `9e21536e4f3e65257e182784f7b1245312eb91eb` |
| Base commit date | 2026-08-19 |
| Latest release visible at planning time | `v0.1.35` |
| Plan date | 2026-08-21 |

The source checkout's unrelated untracked `plans/` and `ui/design/` directories were not copied, modified, staged, or deleted. This worktree was created after fetching `origin/main` and is clean except for this plan.

The external compatibility baseline is the current official OpenAI documentation for:

- [Create a batch](https://developers.openai.com/api/reference/python/resources/batches/methods/create)
- [List batches](https://developers.openai.com/api/reference/python/resources/batches/methods/list)
- [Upload a file](https://developers.openai.com/api/reference/python/resources/files/methods/create)
- [List files](https://developers.openai.com/api/reference/python/resources/files/methods/list)

These links are comparison inputs, not a promise that DeltaLLM implements every OpenAI field or endpoint. The DeltaLLM public specification and compatibility matrix defined below become the authoritative statement of what this deployment supports.

## 2. Executive decision

Build the work in this order:

1. Make the server's existing public behavior explicit with stable Pydantic DTOs, wire-contract tests, and an OpenAI/Anthropic/Delta error-dialect boundary.
2. Correct the concrete interoperability gaps in Files and Batches: multipart form semantics, cursor pages, missing list/delete operations, stable errors, and documented Delta extensions.
3. Publish a curated public OpenAPI document and public Swagger/ReDoc views that exclude admin, authentication, operational, and UI routes.
4. Add official OpenAI Python and TypeScript client conformance tests against the server.
5. Add a small Python package under `clients/python` for DeltaLLM-specific batch workflows and webhook verification. Continue to use the official OpenAI SDK for chat, responses, embeddings, Files, and Batches.
6. Keep the package in the monorepo until the extraction criteria in section 16 are met.

This avoids two expensive mistakes:

- publishing an SDK around an undocumented, unstable wire contract; and
- reimplementing the large OpenAI API surface that DeltaLLM intentionally claims to proxy.

The SDK's initial value is not another chat client. Its value is safe batch submission, bounded waiting, streamed JSONL result handling, typed Delta extensions, idempotency, and webhook verification.

## 3. Audited current state on latest `origin/main`

### 3.1 What already works

- Standard application clients can point the official OpenAI SDK at DeltaLLM by changing `base_url` and the API key; the README and quickstart already show this.
- Public Files support upload, metadata retrieval, and content download.
- Public Batches support create, retrieve, list, and cancel for `/v1/embeddings` and non-streaming `/v1/chat/completions` input lines.
- Batch output and error artifacts already use OpenAI-shaped JSONL rows with `id`, `custom_id`, `response`, and `error`.
- Batch ownership is scoped by API key, team, or organization, with team scope taking precedence over organization scope.
- Batch creation has a durable create-session path and optional `Idempotency-Key` support behind configuration.
- Terminal webhook configuration is validated, encrypted, write-only, SSRF constrained, delivered through a durable outbox, signed, retried, and redacted from logs/audit responses.
- `docs/features/batching.md` documents the operational batch lifecycle and webhook delivery behavior in depth.

These are substantial runtime foundations. The work should preserve them rather than build a parallel batch implementation in an SDK.

### 3.2 Concrete contract gaps

The generated OpenAPI document was inspected directly from `create_app().openapi()` at the base commit:

| Finding | Current evidence | Consequence |
| --- | --- | --- |
| The default document mixes public and private surfaces | 209 paths total; 161 start with `/ui/api` | A developer cannot safely treat `/openapi.json` as the app-facing contract, and generated clients would contain admin APIs. |
| Public success schemas are mostly empty | 16 public `/v1` operations have an empty JSON 200 schema; `/v1/models` is only `additionalProperties: true` | IDE completion, generated docs, validators, and client generators cannot describe responses. |
| Batch create is untyped | `payload: dict[str, Any]`; OpenAPI emits `additionalProperties: true` | Required fields, supported endpoints, metadata, webhook fields, and unsupported behavior are undiscoverable. |
| Files and Batches return manual dictionaries | `src/batch/service.py` and `src/batch/serialization.py` | Static types exist neither at the route boundary nor as a stable reusable contract. |
| File upload does not match normal SDK multipart behavior | `purpose` is currently emitted as a query parameter rather than a multipart form field | The official SDK sends a form field; today the server silently falls back to the default in the common `batch` case. |
| Batch pagination is incomplete | Public list accepts only `limit` and returns only `object` plus `data` | Official cursor-page helpers require `after`, `first_id`, `last_id`, and `has_more`. |
| Existing repository cursor semantics are unsuitable | Internal `after: datetime` filters `created_at > after` while sorting descending; ordering has no ID tie-breaker | It cannot implement object-ID cursor pagination deterministically. |
| Files have no list or public delete route | Only POST, GET metadata, and GET content are registered | The official Files resource is only partially compatible and user cleanup is not ergonomic. |
| Errors have multiple wire envelopes | `ProxyError` uses `{error:{...}}`; `HTTPException` uses `{detail:...}`; validation uses 422 `detail` arrays; webhook validation may use a detail object | Clients must special-case the failure source, and OpenAPI cannot advertise one error model. |
| Authentication is not modeled as security | OpenAPI has no `securitySchemes`; `Authorization` appears as an optional header parameter | Generated clients do not know that Bearer authentication is required. |
| Operation IDs are framework-generated | Names include path-derived suffixes such as `create_batch_v1_batches_post` | Renaming a Python function can create an accidental generated-client breaking change. |
| Full OpenAPI generation already warns about a duplicate admin operation ID | Duplicate UI branding GET/HEAD operation | A generated full client is not a stable release artifact. |
| Public batch fields are only partially represented | The serializer omits `model`, cancellation timestamps, `finalizing_at`, and `usage`; Files omit `expires_at` | The official SDK can often tolerate optional omissions, but DeltaLLM's supported subset is not explicit. |
| Unknown batch-create fields are silently ignored | The raw dict handler selects known values | New OpenAI fields such as `output_expires_after` can appear accepted while having no effect. |
| Cross-scope retrieval returns 403 after finding the object | Files and Batches load by ID, then check access | A caller can distinguish an existing foreign ID from a missing ID. |
| Versioning is disconnected | FastAPI and `pyproject.toml` are statically `0.1.0` while releases are at `v0.1.35` | OpenAPI `info.version` cannot currently communicate either product or contract version reliably. |

### 3.3 Current public-surface classification

The curated contract must classify routes by dialect and audience instead of selecting every FastAPI route with a prefix.

| Surface | Routes | Public spec | Initial companion SDK |
| --- | --- | --- | --- |
| OpenAI-compatible | `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/images/generations`, `/v1/audio/speech`, `/v1/audio/transcriptions`, `/v1/models`, `/v1/files*`, `/v1/batches*` | Yes | Official OpenAI client exposed as `client.openai`; Delta helper adds batch workflow only. |
| Anthropic-compatible subset | `/v1/messages` | Yes, under a distinct tag and error dialect | No wrapper in v0.1; document use of the official Anthropic SDK with Bearer auth. |
| Delta public extensions | `/v1/rerank`, `/mcp`, batch webhook request/response/event fields | Yes, explicitly marked with `x-deltallm-extension` | Webhook verification and batch extension support in v0.1; MCP helper deferred until its DTO is stable. |
| Operational | `/health*`, `/metrics` | No; keep separate operator documentation | No. |
| Control plane and UI | `/ui/api*`, `/auth*`, `/spend*`, `/global*`, SPA routes | No | No. A future admin SDK is a different product and permission model. |
| Provider callback | `/webhooks/email/resend` | No | No. |

## 4. Goals, non-goals, and contract rules

### 4.1 Goals

1. Give developers a machine-readable public contract at a stable URL and human-readable public Swagger/ReDoc pages.
2. Preserve successful response bytes and omission/null behavior while DTOs are first introduced.
3. Make every intentional compatibility difference visible in a checked-in matrix.
4. Make official OpenAI SDK Files/Batches calls and cursor iteration work against DeltaLLM.
5. Give DeltaLLM extensions stable names, schemas, examples, and secret-handling rules.
6. Provide consistent, typed errors for each public protocol dialect.
7. Keep pagination tenant-safe, deterministic, bounded, and one database round trip per page.
8. Avoid adding any data-plane lookup only for documentation or SDK support.
9. Release a small, independently versioned Python package without coupling its release to container or Helm publication.

### 4.2 Non-goals for the first SDK release

- Reimplementing the official OpenAI SDK's chat, Responses, embeddings, images, audio, or Files resources.
- Generating and committing a full client from the current application-wide OpenAPI document.
- Exposing `/ui/api`, platform sessions, master-key administration, spend reports, diagnostics, or deployment topology.
- Claiming every current OpenAI Batch endpoint; DeltaLLM continues to support only the two worker implementations it actually owns.
- Supporting provider-native async batch APIs as a separate public abstraction.
- Adding JavaScript, Go, or Java DeltaLLM packages before the Python package and contract have real consumers.
- Fetching OpenAPI at SDK runtime or making capability discovery a required request path.
- Loading an entire batch input or output file into memory in convenience helpers.

### 4.3 Contract invariants

- `src/public_api/v1` owns public DTO definitions. Persistence dataclasses remain in `src/batch/models.py` and are not public API types.
- Existing domain serializers adapt persisted records into public DTOs; route handlers do not recreate response dictionaries.
- The checked-in `openapi/deltallm-public-v1.json` is generated from route metadata and DTOs, never hand-edited.
- `scripts/export_public_openapi.py --check` proves the checked-in artifact matches code.
- Public route behavior is compatible by default. A deliberate break requires a migration note, a contract-major decision, and an OpenAPI breaking-change review.
- Secret request fields are `writeOnly`; no response schema, example, exception, log, or audit payload contains a webhook URL or signing secret.
- The public schema is deterministic and independent of database contents, provider availability, feature-flag values, or startup lifecycle.
- OpenAPI generation and serving are cached in process and perform no I/O after application construction.
- Each list page is bounded and resolved with one tenant-scoped SQL query.
- Cross-tenant and unknown resource IDs have indistinguishable public behavior.
- The SDK never owns an unbounded background poller and never retries a non-idempotent submission without an idempotency key.

## 5. Target architecture

```text
persisted/domain records
          |
          v
public adapters + Pydantic DTOs  -----> webhook event snapshot
          |
          v
public FastAPI route metadata
          |
          +----> runtime responses
          |
          +----> cached /openapi/public-v1.json
          |
          +----> checked-in openapi/deltallm-public-v1.json
                         |
                         +----> public docs + compatibility checks
                         +----> server conformance tests
                         +----> thin companion SDK contract tests
```

Create these server-side modules rather than enlarging the existing endpoint or batch worker files:

```text
src/public_api/
  __init__.py
  surface.py                 # public route/dialect ownership
  errors.py                  # public exception mapping and error DTOs
  openapi.py                 # deterministic public OpenAPI construction/cache
  v1/
    __init__.py
    common.py                # JSON values, headers, shared enums
    pagination.py
    files.py
    batches.py
    batch_jsonl.py
    webhooks.py
    gateway.py               # non-batch response docs, streaming/binary content
```

`src/api/v1/router.py` should expose a `public_data_router` containing the actual app-facing routes and a separate operational/control composition. `/mcp` remains at its existing runtime path but is explicitly registered in `src/public_api/surface.py`. The public OpenAPI generator consumes the explicit public route collection, not the full app and not a string-prefix filter.

## 6. DTO and wire-format design

### 6.1 Shared DTO policy

- Use Pydantic v2 models with explicit `title`, descriptions, examples, and stable component names.
- Use `ConfigDict(extra="forbid")` for DeltaLLM-owned request bodies once the compatibility cutover is announced. During the first wire-preserving DTO PR, retain the current ignore behavior and add a test that makes this temporary state visible.
- Use `ConfigDict(extra="allow")` for proxied provider responses where compatible providers may add fields. Do not let `response_model` strip unknown upstream fields.
- Use `response_model_exclude_unset=True` or OpenAPI-only response declarations where necessary so adding a DTO does not turn omitted fields into explicit `null` values.
- Timestamps are integer Unix seconds and carry `format: unixtime` in JSON Schema.
- IDs are opaque nonblank strings. Do not require OpenAI's example prefixes because existing DeltaLLM rows use UUID strings.
- Define recursive `JSONValue` once. Metadata remains `dict[str, JSONValue]` in contract v1 to preserve existing behavior; tightening it to OpenAI's 16 string pairs would be a later breaking change.
- Delta-only response fields use normal JSON names plus `x-deltallm-extension: true` in their schema; do not hide them in an untyped extension blob.

### 6.2 File DTOs

| DTO | Fields | Notes |
| --- | --- | --- |
| `FileObject` | `id`, `object="file"`, `bytes`, `created_at`, `filename`, `purpose`, `status`, optional `expires_at`, optional `status_details` | `status` and `status_details` are documented as compatibility/deprecated fields. Add `expires_at` from the existing record; do not synthesize it. |
| `FilePurpose` | `batch`, `batch_output`, `batch_error` for current responses; request accepts only `batch` | Keep internal purpose names explicit. The official Files schema uses `batch_output`, while DeltaLLM currently persists `batch_error` for error artifacts. Validate official SDK parsing; if it rejects the current value, map `batch_error` to public `batch_output` without changing persistence. |
| `FileUploadForm` | binary `file`, `purpose="batch"` | `purpose` is a multipart form field. Preserve the current query parameter for one deprecation window and reject conflicting query/form values. |
| `FileListPage` | `object="list"`, `data`, `first_id`, `last_id`, `has_more` | Empty page returns null IDs and `has_more=false`. |
| `FileDeleted` | `id`, `object="file"`, `deleted=true` | Returned after a logical, durable delete request is committed. |
| `FileContent` | binary/string response under `application/jsonl` | Document `Content-Disposition`, byte streaming, and 404 behavior. Do not model this as JSON `{}`. |

Add `GET /v1/files` with `after`, `limit`, `order`, and `purpose`. Add `DELETE /v1/files/{file_id}` only with the durable deletion design in section 8; do not copy the retention worker's current storage-delete-then-row-delete sequence into a request path.

### 6.3 Batch DTOs

| DTO | Fields | Notes |
| --- | --- | --- |
| `CreateBatchRequest` | `input_file_id`, `endpoint`, `completion_window`, optional `metadata`, optional `webhook` | `endpoint` is a literal union of `/v1/embeddings` and `/v1/chat/completions`. Missing or null `completion_window` normalizes to `24h` to preserve current behavior. |
| `BatchObject` | `id`, `object="batch"`, `endpoint`, `completion_window`, `status`, `input_file_id`, nullable output/error IDs, timestamps, nullable `errors`, `request_counts`, `metadata`, optional `model`, optional `usage`, optional `webhook` | Keep the exact existing required/nullable fields. Add already-authoritative `model`, `cancelling_at` from `cancel_requested_at`, `cancelled_at` from the terminal transition timestamp, and explicit documented null/omission behavior. Leave `finalizing_at`/`usage` absent or null until they have an authoritative persisted value. |
| `BatchStatus` | `validating`, `failed`, `in_progress`, `finalizing`, `completed`, `expired`, `cancelling`, `cancelled` | Preserve the existing mapping from internal `queued` to `validating`. |
| `BatchRequestCounts` | `total`, `completed`, `failed`, optional `cancelled`, optional `in_progress` | The last two are Delta extensions and remain because current users can observe them. |
| `BatchErrors` / `BatchErrorItem` | OpenAI-compatible list and item fields | The value remains null for DeltaLLM's synchronous create validation until a real batch-level validation-error source exists. |
| `BatchUsage` | compatible optional aggregate fields | Schema may be present while the response omits the field. Do not calculate it with a read-time scan. A future finalization-time aggregate can populate it. |
| `BatchListPage` | `object="list"`, `data`, `first_id`, `last_id`, `has_more` | Supports official SDK cursor helpers. |

Do not silently accept `output_expires_after` or unsupported OpenAI batch endpoints. After the transitional DTO PR, return a stable `invalid_request_error` with `code="unsupported_parameter"` and `param` naming the field. The compatibility matrix must list supported endpoints, current maximum file/item sizes from runtime configuration, and unsupported optional OpenAI fields.

### 6.4 Batch JSONL DTOs

OpenAPI cannot natively validate every line of an uploaded/downloaded JSONL document, so publish component schemas and examples and reference them from the Files/Batches descriptions.

| DTO | Shape |
| --- | --- |
| `EmbeddingBatchInputLine` | `custom_id`, optional/default `method="POST"`, optional/default `url="/v1/embeddings"`, `body: EmbeddingRequest` |
| `ChatBatchInputLine` | Same wrapper with `/v1/chat/completions` and a non-streaming `ChatCompletionRequest` body |
| `BatchSuccessResponse` | `status_code`, `request_id`, `body` |
| `BatchOutputLine` | `id`, `custom_id`, non-null `response`, `error=null` |
| `BatchErrorLine` | `id`, `custom_id`, `response=null`, typed `error` |

Cross-field validation remains in `src/batch/request_validation.py`: line URL must match the batch endpoint, method must be POST, `custom_id` must be unique, streaming must be false, and MCP tools remain unsupported in batch chat. The DTO documentation and runtime validator must use the same supported-endpoint constants.

### 6.5 Webhook DTOs

| DTO | Fields | Security behavior |
| --- | --- | --- |
| `BatchWebhookCreate` | `url`, `signing_secret` | Entire object is request-only. Both fields are `writeOnly`; the secret is `format: password`, excluded from repr, examples, validation input, and exception context. Reuse the existing validator rather than introduce a second URL policy. |
| `BatchWebhookConfigured` | `configured=true` | The only webhook value allowed in a Batch response. |
| `BatchWebhookEvent` | `id`, `object="event"`, terminal `type`, `created_at`, `data.batch` | Reuse the exact `BatchObject` snapshot serializer used by GET Batches. |
| `BatchWebhookHeaders` | event ID/type/attempt/timestamp/signature and `Idempotency-Key` | Document as callback headers, not ordinary response properties. |

The schema must state that webhook delivery is at least once, event ID/body are stable across retries/replay, signature timestamp changes per attempt, and a receiver verifies the signature over raw bytes before JSON parsing.

### 6.6 Gateway DTO completion

The curated public spec must not publish empty schemas for the rest of the gateway. Complete these without forcing response filtering:

- reference the existing request DTOs for Chat, Completions, Responses, Embeddings, Images, Audio, and Rerank;
- define known response DTOs with `extra="allow"` for provider-compatible additions;
- describe non-stream JSON and `text/event-stream` as separate 200 media types for streaming endpoints;
- describe audio speech as binary with its supported media types;
- model transcription text and JSON response variants separately;
- keep Anthropic Messages response/error components in an Anthropic-tagged namespace;
- define JSON-RPC request/result/error components for `/mcp`, including its 202 notification response;
- replace `/v1/models`' broad return annotation with the existing `ModelsResponse` contract while preserving output bytes.

Where FastAPI `response_model` would filter an upstream response, use the route's `responses={...}` OpenAPI declaration instead. Contract documentation must never mutate the proxied payload.

## 7. Public error contract

### 7.1 Dialect-aware mapping

Create one path-to-dialect classifier in `src/public_api/surface.py` and one public exception mapping implementation in `src/public_api/errors.py`.

- OpenAI-compatible and Delta JSON REST routes use:

  ```json
  {
    "error": {
      "message": "...",
      "type": "invalid_request_error",
      "param": "input_file_id",
      "code": "file_not_found"
    }
  }
  ```

- `/v1/messages` keeps the Anthropic-compatible error envelope.
- `/mcp` keeps JSON-RPC errors for protocol methods; authentication failures before JSON-RPC dispatch use a documented HTTP error.
- Non-public routes continue using their current handlers. Do not globally turn `/ui/api` errors into OpenAI errors.

### 7.2 Status and code table

Define named public exceptions and document at least:

| Status | Type | Stable example codes |
| --- | --- | --- |
| 400 | `invalid_request_error` | `invalid_jsonl`, `unsupported_endpoint`, `unsupported_parameter`, `invalid_completion_window`, `invalid_webhook` |
| 401 | `authentication_error` | `missing_api_key`, `invalid_api_key` |
| 404 | `not_found_error` | `file_not_found`, `batch_not_found`, `batch_api_disabled` |
| 409 | `conflict_error` | `idempotency_conflict`, `file_in_use` |
| 413 | `invalid_request_error` | `file_too_large`, `line_too_large` |
| 429 | `rate_limit_error` | `rate_limit_exceeded`, `batch_capacity_exceeded` |
| 503 | `service_unavailable` | `batch_unavailable`, `storage_unavailable`, `webhook_unavailable` |

Convert public Pydantic validation to the stable envelope and HTTP 400 in Slice 2, which is the compatibility behavior SDK callers expect. Treat this as a deliberate 0.x public-contract cutover: call it out in release notes and golden tests, but do not carry two validation envelopes or an ambiguous runtime toggle.

Never return a foreign resource as 403. Public Files/Batches retrieve, content, cancel, cursor-anchor, and delete paths return the same 404 as an unknown ID. Audit still records the denied attempt with safe IDs and scope metadata.

### 7.3 Authentication schema

Use FastAPI's `HTTPBearer(auto_error=False)` through `Security(...)` so protected public operations emit a named `BearerAuth` security scheme instead of an optional free-form header. Preserve runtime master-key, virtual key, JWT, and custom-auth behavior by continuing to pass the raw token into `authenticate_request`.

Tests must prove:

- no duplicate auth read or database lookup is introduced;
- absent, empty, malformed, invalid, and valid Bearer tokens keep the intended status;
- public OpenAPI marks protected routes and does not mark health/operator routes accidentally;
- secrets are never included in validation diagnostics.

## 8. Deterministic pagination and file deletion

### 8.1 Batch pages

Change the repository interface to return a page result, not a bare list:

```text
BatchPageResult
  items: list[BatchJobRecord]
  has_more: bool
```

The public request is `after=<last batch id>&limit=1..100`, default 20, descending by creation time. Fetch `limit + 1`, return at most `limit`, and calculate `has_more` from the extra record.

Use one SQL statement with:

- the current effective API-key/team/organization scope predicate;
- an anchor subquery scoped with the identical predicate;
- keyset comparison `(created_at, batch_id) < (anchor.created_at, anchor.batch_id)`;
- `ORDER BY created_at DESC, batch_id DESC`;
- a limit of `requested + 1`.

An absent or foreign anchor yields an empty page and reveals no resource existence. It does not fall back to the first page. Add composite indexes for each supported owner scope ending in `(created_at DESC, batch_id DESC)`. Do not use offset pagination or a second anchor query.

### 8.2 File pages

Add the equivalent `FilePageResult` and repository query with:

- `after` object-ID cursor;
- `limit` default 100 and maximum 1,000, explicitly documented as DeltaLLM's bounded deviation from OpenAI's larger Files page limit;
- `order=asc|desc` with deterministic `file_id` tie-breaking;
- optional purpose filter;
- the same effective ownership rules as file retrieval;
- `limit + 1` page calculation in one query.

Compatibility means the SDK can page correctly, not that a self-hosted gateway must copy OpenAI's 10,000-row default. Load tests may justify a future additive limit increase, but contract v1 starts at the bounded values above.

### 8.3 Durable public file delete

Do not delete object storage synchronously and then delete the database row. Add a small durable deletion outbox and make retention cleanup use the same path:

```text
deltallm_batch_file_deletion_outbox
  file_id              TEXT PRIMARY KEY
  storage_backend      TEXT NOT NULL
  storage_key          TEXT NOT NULL
  status               TEXT NOT NULL  # queued, processing, retrying, failed
  attempt_count        INTEGER NOT NULL DEFAULT 0
  next_attempt_at      TIMESTAMP NULL
  locked_by            TEXT NULL
  lease_expires_at     TIMESTAMP NULL
  last_error           TEXT NULL       # bounded classification only
  created_at           TIMESTAMP NOT NULL
  updated_at           TIMESTAMP NOT NULL
```

The DELETE transaction must:

1. lock the tenant-scoped file;
2. return 404 for missing/foreign IDs;
3. check all batch-job and create-session references;
4. return 409 `file_in_use` if any reference remains;
5. insert the idempotent deletion record with a storage snapshot;
6. make subsequent public GET/list calls treat the file as deleted; and
7. return `FileDeleted` after commit.

A bounded worker performs idempotent storage deletion, then removes the file row and outbox row transactionally where possible. Process death, storage timeout, an already-missing object, retries, lease recovery, and backend migration all need tests. Reuse lifecycle/retry/observability patterns from the webhook outbox without sharing secret material or copying a worker wholesale.

`init_batch_runtime` owns this lifecycle. API-role processes enqueue only; the existing `embeddings_batch_gc_enabled` cleanup-owning batch worker role claims and drains deletions in split deployments. The deployment gate must verify at least one such worker is healthy before a server release exposing DELETE is rolled out to API pods.

This slice also moves retention-triggered unreferenced files onto the deletion outbox so the repository has one deletion policy.

## 9. Public OpenAPI and developer documentation

### 9.1 Runtime endpoints

Keep existing FastAPI defaults for backward compatibility and add:

| Route | Purpose |
| --- | --- |
| `GET /openapi/public-v1.json` | Curated, cached public contract only. |
| `GET /docs/api` | Swagger UI configured to load the curated public contract. |
| `GET /redoc/api` | ReDoc configured to load the curated public contract. |

These documentation routes use `include_in_schema=False`. They do not require a database or a working provider. The schema describes Bearer auth but remains readable without a token unless deployment policy explicitly disables public docs.

### 9.2 Specification metadata

- `info.title`: `DeltaLLM Public API`
- `info.version`: independent public contract SemVer, starting at `1.0.0-beta.1` until compatibility gates pass
- `servers`: examples only; do not bake deployment hostnames into the artifact
- stable tag descriptions for OpenAI compatibility, Anthropic compatibility, and Delta extensions
- stable explicit operation IDs such as `files.create`, `files.list`, `batches.create`, `batches.list`, and `mcp.call`
- `BearerAuth` security scheme
- reusable error responses and common request ID/rate-limit headers
- `x-deltallm-compatibility`, `x-deltallm-extension`, and `x-deltallm-feature-flag` annotations where useful

Do not use the server image tag as the OpenAPI contract version. Record the minimum DeltaLLM server release in the compatibility matrix and SDK metadata instead.

### 9.3 Checked-in artifact and CI

Add:

```text
openapi/deltallm-public-v1.json
scripts/export_public_openapi.py
tests/contracts/test_public_openapi.py
tests/contracts/test_public_wire_examples.py
docs/api/public-contract.md
docs/api/compatibility.md
```

The exporter writes canonical UTF-8 JSON with sorted keys and a final newline. `--check` exits nonzero and prints the first differing logical path when regeneration is required.

CI gates:

1. build the schema twice and prove byte-for-byte determinism;
2. prove the checked-in artifact is current;
3. validate the OpenAPI document;
4. reject admin/auth/health/metrics/spend/UI paths;
5. reject duplicate operation IDs;
6. reject protected operations without `BearerAuth`;
7. reject empty 2xx schemas or undocumented binary/SSE bodies;
8. reject `signing_secret` without `writeOnly` and reject webhook secret examples;
9. run a pinned OpenAPI breaking-change checker against the base-branch artifact after the initial baseline exists;
10. require an explicit reviewed waiver plus contract-major plan for a breaking change.

The public spec is the developer discovery endpoint. The checked-in copy is the review/release artifact. Both come from the same code, so they cannot become competing hand-maintained sources.

### 9.4 Human documentation

Update `mkdocs.yml`, `README.md`, `docs/getting-started/quickstart.md`, `docs/api/proxy.md`, and `docs/features/batching.md` to include:

- links to `/docs/api`, `/redoc/api`, and `/openapi/public-v1.json`;
- official OpenAI Python and TypeScript Files/Batches examples against a DeltaLLM base URL;
- DeltaLLM batch limits as configuration-dependent values, not hard-coded universal promises;
- the supported endpoint/field matrix and known differences;
- idempotency configuration and retry semantics;
- cursor iteration examples;
- JSONL input/output schemas and streamed processing examples;
- webhook signing, raw-body verification, timestamp tolerance, deduplication, and replay behavior;
- error envelopes with request IDs and retry guidance;
- the companion SDK as optional convenience, not a prerequisite for gateway use.

Resolve the current documentation mismatch between the 50,000-item/200 MB production example and the 10,000-item/52 MB defaults by labeling each value with its actual configuration source.

## 10. Official client conformance suite

Before publishing the companion package, prove that the standard clients work without it.

### 10.1 Python matrix

Create `tests/compat/openai_python/` and run the oldest declared supported official OpenAI Python SDK plus the version locked on main. The tests must use only public SDK methods and request options.

Cover:

1. `OpenAI(base_url=".../v1", api_key=...)` initialization;
2. file upload with multipart `purpose=batch`;
3. file retrieve and streamed content download;
4. batch create, retrieve, cancel, and list;
5. auto-pagination across at least three pages with equal `created_at` values;
6. Delta webhook payload through the SDK's documented `extra_body` mechanism;
7. `Idempotency-Key` through documented `extra_headers`;
8. parsing Delta request-count and webhook extension fields without losing standard fields;
9. typed error handling for 400, 401, 404, 409, 429, and 503;
10. no secret in exception text/repr.

If a tested official SDK version does not preserve extension response fields, the companion helper may return its own Delta extension view, but the server must not distort the standard Batch object to accommodate one client implementation.

### 10.2 TypeScript matrix

Create an isolated locked fixture under `tests/compat/openai_typescript/`. Cover upload, batch create/retrieve/list pagination, content, extension request options, and error parsing. Do not add the OpenAI package to the admin UI bundle.

### 10.3 Test server

Run compatibility tests against a real local ASGI/HTTP server with:

- real PostgreSQL for cursor and ownership behavior;
- a deterministic local artifact backend;
- a deterministic fake upstream for embeddings/chat;
- one API-key scope, one same-team key, one same-organization key, and a foreign scope;
- fixed clocks/fixtures for equal-timestamp cursor cases;
- batching enabled and idempotency enabled;
- webhook delivery disabled unless the test explicitly owns a safe local receiver.

Do not mock the HTTP serialization layer in the official-client tests.

## 11. Python companion SDK

### 11.1 Location and package identity

Add an independently buildable project:

```text
clients/python/
  pyproject.toml
  README.md
  CHANGELOG.md
  src/deltallm/
    __init__.py
    _client.py
    _async_client.py
    _batch_workflow.py
    _webhooks.py
    _errors.py
    types.py
    py.typed
  tests/
```

Working distribution name: `deltallm-sdk`; import package: `deltallm`. Registry ownership/availability must be verified before the first public publish. Do not rename the server distribution or move server source as part of this work.

The SDK has its own SemVer, changelog, build metadata, lock file, release workflow, and tags such as `sdk-python-v0.1.0`. It declares a tested range of the official `openai` package and Python 3.11+.

### 11.2 Public API

The intended ergonomic surface is:

```python
from deltallm import DeltaLLM, WebhookConfig

with DeltaLLM(base_url="https://gateway.example/v1", api_key="...") as client:
    # Standard API: the official OpenAI client.
    response = client.openai.responses.create(model="...", input="...")

    # Delta value-add: a bounded batch workflow.
    handle = client.batch.submit_jsonl(
        path="requests.jsonl",
        endpoint="/v1/embeddings",
        idempotency_key="job-2026-08-21",
        webhook=WebhookConfig(url="https://...", signing_secret="..."),
    )
    terminal = handle.wait(timeout=3600)
    for row in handle.iter_results():
        process(row)
```

The v0.1 public names are `DeltaLLM`, `AsyncDeltaLLM`, `WebhookConfig`, `.openai`, and `.batch`. Lock them with import/API tests before the preview release; later changes follow SDK SemVer.

### 11.3 Implementation constraints

- `client.openai` is an official `OpenAI`/`AsyncOpenAI` instance, not a fork or subclass.
- Batch standard operations call documented official SDK resource methods.
- Delta request fields use documented `extra_body`; idempotency uses documented `extra_headers`. Add a conformance test for every supported official SDK version.
- Do not call private `_client` methods or depend on generated internal OpenAI classes.
- If the caller supplies an official client, DeltaLLM does not close it. If DeltaLLM constructs it, the context manager owns and closes it.
- Do not create a second HTTP connection pool for normal operation.
- Sync and async APIs have feature parity and separate tests.
- `submit_jsonl` streams/spools input, validates lines incrementally, and never buffers an unbounded file.
- Submission generates or requires an idempotency key and reuses it for retries. Surface the key safely to the caller but never log the API key or webhook secret.
- `wait` requires a finite timeout/deadline, uses monotonic time, honors `Retry-After`, applies bounded jitter, and stops on every terminal Batch status.
- `iter_results` streams file content line by line, validates each row, preserves `custom_id`, and closes the response on early exit/cancellation.
- HTTP retries remain owned by the official SDK. Workflow polling controls only the interval between retrieve calls.
- Webhook verification is pure and constant-time, accepts raw bytes, enforces timestamp tolerance before parsing, supports the `v1=` signature, and returns a typed verified event.
- Typed SDK exceptions retain status, request ID, code, and retry-after without embedding response bodies that may contain sensitive caller data.
- Ship `py.typed` and pass strict type checks for the package's public modules.

### 11.4 Generated versus handwritten code

Do not generate a second full OpenAI client. Keep the workflow/resource code handwritten and small. Generate nothing in the initial SDK unless a concrete extension model cannot be kept aligned through the OpenAPI contract tests.

The canonical model source remains the server's public OpenAPI document. SDK tests load the checked-in schema and assert the small handwritten extension types and examples remain compatible. If extension types grow enough that this becomes repetitive, add a deterministic tag-filtered type-generation step in a later SDK minor; do not start with thousands of generated OpenAI files.

## 12. Implementation sequence

Each slice should be independently reviewable and deployable. Do not combine public error cutover, pagination migrations, OpenAPI publication, and package publishing in one PR.

### Slice 0 — Baseline and contract inventory

Purpose: freeze current behavior before DTOs can accidentally alter it.

Changes:

- add golden HTTP fixtures for successful/error Files and Batches responses, multipart requests, JSONL rows, webhook events, headers, and omitted/null fields;
- add an audited route inventory with audience and dialect classifications;
- add tests that record the current OpenAPI gaps without yet requiring the final schema;
- add current official OpenAI Python/TypeScript smoke fixtures for upload and basic batch calls, marked expected-failure only for named known gaps;
- record actual configured batch default and recommended production limits in the compatibility matrix draft.

Primary files:

- `tests/contracts/fixtures/*`
- `tests/contracts/test_public_wire_examples.py`
- `tests/compat/openai_python/*`
- `tests/compat/openai_typescript/*`
- `docs/api/compatibility.md`

Exit gate: every later wire change produces an intentional golden diff; expected failures name one missing capability each and cannot mask unrelated failures.

### Slice 1 — Public DTO foundation with zero wire changes

Purpose: establish typed models and one serializer without changing HTTP bytes.

Changes:

- add `src/public_api/v1` DTO modules from section 6;
- make `serialize_public_batch` and file serialization validate through the DTOs;
- reuse the same batch DTO for webhook event snapshots;
- add explicit OpenAPI response models/declarations to Files and Batches with unset-field exclusion;
- keep temporary unknown-field ignore semantics and current error status/envelopes;
- add schema unit tests, serializer parity tests, and secret-repr tests.

Primary files:

- `src/public_api/v1/*`
- `src/batch/serialization.py`
- `src/batch/service.py`
- `src/batch/webhooks/events.py`
- `src/api/v1/endpoints/files.py`
- `src/api/v1/endpoints/batches.py`
- focused batch/webhook tests

Exit gate: every successful Files/Batches golden response is byte-equivalent; their OpenAPI 2xx schemas are nonempty; webhook secret tests still pass.

### Slice 2 — Request and error compatibility cutover

Purpose: make requests/errors predictable and official-client compatible.

Changes:

- accept `purpose` as multipart form data; preserve query compatibility for one documented deprecation window;
- enforce `purpose=batch` for user uploads;
- switch batch create to `CreateBatchRequest` and reject unsupported fields after the announced transition;
- add the authoritative additive response fields (`FileObject.expires_at`, `BatchObject.model`, and cancellation timestamps) with explicit omission/null golden tests;
- add dialect-aware public HTTP/validation exception handlers;
- replace foreign-resource 403 responses with non-enumerating 404s;
- add Bearer security metadata through `Security(HTTPBearer)` without an extra auth lookup;
- assign explicit operation IDs and documented response/error headers.

Primary files:

- `src/public_api/errors.py`
- `src/public_api/surface.py`
- `src/middleware/auth.py`
- `src/middleware/errors.py`
- Files/Batches endpoints and service exceptions
- `docs/api/compatibility.md`

Exit gate: official client upload/create work; all public failure classes match the code/status table; admin/UI error fixtures are unchanged; secret/non-enumeration tests pass.

### Slice 3 — Batch cursor pages

Purpose: enable stable official SDK list iteration.

Changes:

- replace datetime pagination with object-ID keyset pagination;
- return `BatchListPage` metadata;
- add scoped composite indexes through an additive Prisma migration;
- add same-timestamp, insert-between-pages, empty-page, foreign-anchor, and concurrent-list integration tests;
- keep one SQL round trip per page and measure query plans at representative cardinality.

Primary files:

- `src/batch/repositories/job_repository.py`
- `src/batch/repository.py`
- `src/batch/service.py`
- `src/api/v1/endpoints/batches.py`
- `prisma/schema.prisma`
- new migration and repository/integration tests

Exit gate: official Python and TypeScript auto-pagination visit each visible batch exactly once in the fixed dataset; query plan uses a scoped index; fresh and last-release migrations pass.

### Slice 4 — File list and durable delete

Purpose: complete the batch-related Files lifecycle safely.

Changes:

- add tenant-scoped file cursor repository and `GET /v1/files`;
- add the deletion outbox, claim/lease/retry worker, metrics, and recovery logic;
- add idempotent `DELETE /v1/files/{id}`;
- route retention cleanup through the same durable deletion policy;
- document reference conflicts and eventual physical deletion.

Primary files:

- `src/batch/repositories/file_repository.py`
- new focused deletion-outbox repository/service/worker modules
- `src/batch/cleanup.py`
- `src/api/v1/endpoints/files.py`
- Prisma schema/migration
- bootstrap lifecycle/config/metrics modules
- focused unit and real PostgreSQL/storage-failure tests

Exit gate: process death at every state boundary is replay-safe; referenced files are never deleted; public list/delete work through official clients; cleanup does not create orphaned DB/object-store state.

### Slice 5 — Complete curated public OpenAPI

Purpose: provide the actual public discovery endpoint requested by developers.

Changes:

- split route composition into explicit public and non-public collections without changing runtime paths;
- finish nonempty gateway, streaming, binary, Anthropic, MCP, and Delta extension schemas;
- add cached public OpenAPI and public Swagger/ReDoc routes;
- add deterministic exporter, checked-in artifact, lints, and breaking-change CI;
- fix public operation IDs without requiring the unrelated admin duplicate-ID cleanup.

Primary files:

- `src/api/v1/router.py`
- `src/public_api/openapi.py`
- `src/public_api/surface.py`
- relevant public routers/DTOs
- `src/main.py`
- `scripts/export_public_openapi.py`
- `openapi/deltallm-public-v1.json`
- `.github/workflows/ci.yml`
- contract tests

Exit gate: public spec contains only classified public routes, no empty successful response bodies, stable operation IDs, correct media types/security, and no secret-bearing response schema.

### Slice 6 — Server conformance and documentation GA gate

Purpose: prove the contract is usable before adding a branded package.

Changes:

- remove named expected failures from official Python/TypeScript client suites;
- run a real lifecycle: upload, create, page, retrieve, finish/cancel, stream output, delete when unreferenced;
- publish compatibility matrix, request/error examples, and public schema links;
- add load measurements for page sizes and schema generation;
- mark the public contract `1.0.0` only after the gates pass.

Exit gate: the documented standard workflow needs no Delta SDK; CI protects the public schema; operator routes remain excluded.

### Slice 7 — Python SDK preview

Purpose: add Delta-specific ergonomics without duplicating the gateway.

Changes:

- scaffold `clients/python` and its isolated tests/build;
- implement sync/async client ownership, batch workflow, streaming result iterator, and webhook verifier;
- test against the same real conformance server and public schema artifact;
- publish docs/examples but do not publish to PyPI until package API review and registry ownership are complete.

Exit gate: package builds reproducibly, type checks, passes sync/async lifecycle/cancellation/secret tests, and uses no private OpenAI SDK API.

### Slice 8 — SDK release automation and v0.1 publish

Purpose: release the package independently and safely.

Changes:

- add `.github/workflows/sdk-python.yml` for `sdk-python-v*` tags;
- build sdist/wheel, inspect metadata, install each artifact in a clean environment, run a smoke test, and publish with PyPI trusted publishing plus attestations;
- add tag guards to the existing release-artifacts workflow so an SDK release cannot publish server images/Helm/Railway artifacts;
- generate release notes from the SDK changelog and record the minimum tested server version/public contract version.

Exit gate: a test-index release installs and completes the sample workflow; a production publish requires a protected GitHub environment approval; server release jobs are skipped for SDK tags.

## 13. Verification matrix

Run focused checks first and the broader gate required by each slice.

### Every server change

```bash
uv run ruff check <touched Python paths>
uv run ruff format --check <touched Python paths>
uv run pytest <focused tests>
git diff --check
```

### Contract/OpenAPI changes

```bash
uv run python scripts/export_public_openapi.py --check
uv run pytest tests/contracts tests/compat/openai_python
npm --prefix tests/compat/openai_typescript ci
npm --prefix tests/compat/openai_typescript test
git diff --check
```

Also run the pinned OpenAPI validator and breaking-change command installed by the implementation PR. The tool and version must be explicit in CI and locally reproducible.

### Pagination or deletion persistence changes

```bash
uv run prisma generate --schema=./prisma/schema.prisma
uv run python scripts/verify_migration_paths.py --base-ref v0.1.35
uv run pytest tests/test_batch_repository.py tests/test_batch_db_integration.py tests/test_batch_service.py
uv lock --check
git diff --check
```

Add real PostgreSQL concurrency and last-release-upgrade cases. Deletion also requires real or failure-injecting storage tests for timeout, already missing, permission failure, retry, lease expiry, recovery, and shutdown cancellation.

### SDK changes

```bash
uv sync --project clients/python --extra dev
uv run --project clients/python ruff check clients/python
uv run --project clients/python ruff format --check clients/python
uv run --project clients/python pytest clients/python/tests
uv build --project clients/python
uv lock --project clients/python --check
git diff --check
```

Install the built wheel and sdist independently, import `deltallm`, verify `py.typed`, and run the public example against the conformance server. Add the selected strict type checker to the SDK dev dependencies and CI rather than assuming Ruff performs type checking.

### Full pre-release gate

- full backend suite with PostgreSQL and Redis;
- public OpenAPI determinism/lint/breaking-change checks;
- Python and TypeScript official-client suites;
- Python companion SDK suite and artifact install tests;
- container startup smoke proving public docs are available without DB-backed schema generation;
- docs build and link/path/command parity review;
- secret scan over generated OpenAPI, examples, wheel, sdist, logs, and test snapshots.

## 14. Rollout, compatibility, and observability

### 14.1 Rollout order

1. Deploy wire-preserving DTOs and observe validation/serialization metrics.
2. Deploy multipart dual-read and new error envelope with explicit release notes; retain the query-purpose compatibility window.
3. Deploy additive pagination indexes before or with cursor reads. Old servers ignore new indexes; new servers remain compatible with old rows.
4. Enable Files list first. Enable public delete only after at least one deletion worker is healthy and outbox metrics are visible.
5. Publish the beta public spec, run consumer trials, then mark contract 1.0.
6. Publish SDK 0.1 only against a released server version containing contract 1.0.

### 14.2 Compatibility policy

- Public contract major versions are represented by distinct immutable artifacts/URLs if a breaking major is ever required.
- Additive fields and endpoints are minor changes; clarifications and schema corrections without wire change are patches.
- Removing/renaming a field, tightening accepted input, changing omitted to null, changing status/envelope, or altering pagination order is breaking unless covered by a documented deprecation window.
- The SDK supports an explicit minimum server release and public contract major; it does not guess server internals.
- Delta extensions remain optional in the standard OpenAI workflow.

### 14.3 Metrics and logs

Use bounded labels only:

- public validation failures by operation ID, dialect, status, and stable code;
- page size and query latency by resource/scope type, never scope ID;
- cursor outcome (`first`, `next`, `exhausted`, `invalid_or_foreign`) without cursor value;
- deletion outbox queue/due/oldest age, attempts, retries, lease recovery, and terminal failures;
- SDK workflow metrics are opt-in hooks owned by the application, not built-in network telemetry.

Logs may include request ID, operation ID, resource type, status, bounded code, and duration. They must not include API keys, metadata values, JSONL bodies, webhook URL/secret, raw signature, or response bodies.

### 14.4 Rollback

- DTO/OpenAPI code can roll back without data changes because the first slice preserves wire behavior.
- Pagination migrations are additive and stay in place during rollback.
- During mixed-version rollout, new page response fields are additive; clients must tolerate an old pod only if rollout policy permits mixed versions. Prefer completing the server rollout before SDK publication.
- Do not roll back past the public-delete feature while queued deletion rows exist. Drain or leave a compatible worker running first.
- SDK releases are independent. Yank only a broken package release; do not mutate an already published version.

## 15. Definition of done

The public-contract program is complete when:

- `/openapi/public-v1.json`, `/docs/api`, and `/redoc/api` exist and expose no private route;
- every public success/error body and media type is described without filtering actual proxy responses;
- Files and Batches DTOs are the shared source for HTTP responses, webhook events, examples, and tests;
- upload form semantics, Batch pages, File pages, and safe File delete pass official Python/TypeScript client tests;
- page ordering is deterministic and tenant scoped with one SQL query;
- foreign/missing resources are non-enumerating;
- errors are stable by dialect and have documented codes/retry behavior;
- webhook secrets remain write-only through validation, logging, OpenAPI, artifacts, and SDK reprs;
- the checked-in spec is deterministic and protected by breaking-change CI;
- docs include a compatibility matrix and both official-client and optional Delta SDK examples;
- the Python SDK uses public official OpenAI SDK APIs, owns no duplicate pool, has bounded polling/streaming, and has sync/async parity;
- SDK artifacts build, install, type check, and publish independently without triggering server releases;
- all focused and proportionate repository gates pass, including migration paths for persistence changes; and
- no separate SDK repository is required to use or release the first package.

## 16. When to create a separate SDK repository

Re-evaluate extraction only after at least two of these are true:

1. two or more Delta-specific language SDKs have active maintainers and independent CI needs;
2. SDK releases regularly occur without server changes and server releases regularly occur without SDK changes;
3. external contributors need package write/release access but should not have equivalent server-repository access;
4. generated artifacts or multi-language tooling materially slow the server repository's CI/review workflow;
5. the public contract has remained stable across at least two server minor releases and SDK 1.0 is being planned;
6. package adoption and issue volume justify a dedicated roadmap, triage, security policy, and release ownership.

If extraction is approved, preserve history for `clients/` with a filtered repository split, keep the existing package names and tags, move contract fixtures with the clients, and leave server conformance tests in this repository. The public OpenAPI artifact remains owned and released by the server; the SDK repository consumes released artifacts and must not become the source of truth for server behavior.

## 17. Immediate next action

Slice 0 is the first implementation PR. It creates the compatibility baseline needed to review every later decision safely and gives an early answer to the highest-risk question: which official OpenAI SDK calls fail today, and exactly why. Do not scaffold or publish the companion package before that evidence exists.
