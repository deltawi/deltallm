# Issue 95 Plan: OpenAI-Compatible Batch Output For Embeddings

## Objective

Make `/v1/embeddings` batch output artifacts OpenAI Batch-compatible without changing the existing batch lifecycle, file download route, or unrelated provider behavior.

Target outcome:

- success rows in output artifacts use the OpenAI Batch envelope
- the embedding payload is nested under `response.body`
- top-level `id`, `custom_id`, `response`, and `error` are present on every row
- provider-specific `_provider` metadata does not leak into the public compatibility artifact
- single-item and microbatched executions serialize to the same public shape

Related issue:

- [#95](https://github.com/deltawi/deltallm/issues/95)

Recommended branch:

- `fix/95-openai-batch-output-compat`

## Problem Statement

Current finalized output rows are emitted directly from the batch worker as:

```json
{"custom_id":"...","response":{...}}
```

Current finalized error rows are emitted as:

```json
{"custom_id":"...","error":{...}}
```

That is not OpenAI Batch-compatible because the outer row is missing:

- `id`
- `error` on success rows
- `response.status_code`
- `response.request_id`
- `response.body`

It also exposes `_provider` inside the embedding payload even though that is internal/provider-specific metadata rather than part of the OpenAI-compatible artifact contract.

## Scope

In scope:

1. Fix only the public artifact row shape for `/v1/embeddings` batch output/error JSONL files.
2. Introduce one serializer/builder for public batch artifact rows.
3. Remove `_provider` from public embedding artifact payloads.
4. Add targeted regression tests for single-item, microbatch, failure, and cancellation rows.

Out of scope:

1. Broad batch API redesign.
2. New batch endpoint support beyond `/v1/embeddings`.
3. Unrelated provider uniformity cleanup.
4. UI changes.

## Design Decisions

### 1. Keep the fix artifact-focused

This issue is about compatibility of the downloaded JSONL artifacts, not the batch job state machine.

Do not change:

- batch status transitions
- retention behavior
- file download route or MIME type
- request authorization or ownership behavior

### 2. Use a dedicated artifact-row serializer

Do not keep composing public rows inline in `_iter_output_lines()` and `_iter_error_lines()`.

Add one serializer helper that receives a `BatchItemRecord` and emits the public row shape. This is the lowest-risk way to prevent future schema drift.

Recommended location:

- `src/batch/worker.py` first, if we want the smallest patch
- or a dedicated helper module if the logic grows beyond a few small functions

### 3. Generate deterministic synthetic IDs for managed internal batches

Managed internal execution does not currently have upstream OpenAI Batch request ids, so the public artifact needs deterministic synthetic values.

Recommended rule:

- row `id`: `batch_req_{item_id}`
- success `response.request_id`: `req_batch_{item_id}`
- success `response.status_code`: `200`

These values should be documented as synthetic compatibility fields for internally managed execution.

### 4. Preserve the endpoint payload under `response.body`

Existing completed items already persist the embedding endpoint payload in `response_body`.

Use that value as:

- `response.body` for successful rows

Do not wrap or mutate the payload beyond removing internal-only fields that are not part of the public compatibility contract.

### 5. Strip internal-only provider metadata from public artifacts

`_provider` is useful for internal accounting/debugging, but it should not be present in the downloaded public artifact that claims OpenAI compatibility.

Keep `_provider` available for internal hooks/persistence if needed, but remove it from the serialized JSONL row body returned to users.

### 6. Use a consistent row envelope for failed and cancelled items

Every emitted row should contain:

- `id`
- `custom_id`
- `response`
- `error`

Recommended failure/cancellation shape for this fix:

- `response: null`
- `error`: existing normalized error payload plus any available code/type/message fields

This avoids blocking the compatibility fix on a larger migration to persist upstream HTTP failure bodies.

If later we want richer failure envelopes with HTTP status/body details, that can be a follow-up without changing the success-path contract again.

## Concrete Implementation Plan

### Phase 1: Lock the target row contract

Define one explicit public row contract for artifacts produced by `/v1/embeddings` batches.

Success row:

```json
{
  "id": "batch_req_<item_id>",
  "custom_id": "<custom_id>",
  "response": {
    "status_code": 200,
    "request_id": "req_batch_<item_id>",
    "body": {
      "object": "list",
      "data": [
        {
          "object": "embedding",
          "index": 0,
          "embedding": [0.1, 0.2]
        }
      ],
      "model": "text-embedding-3-small",
      "usage": {
        "prompt_tokens": 10,
        "total_tokens": 10
      }
    }
  },
  "error": null
}
```

Failure/cancel row:

```json
{
  "id": "batch_req_<item_id>",
  "custom_id": "<custom_id>",
  "response": null,
  "error": {
    "message": "...",
    "type": "..."
  }
}
```

### Phase 2: Build serializer helpers

Add small helpers for:

1. building the synthetic public row id
2. building the synthetic public request id
3. sanitizing the stored embedding payload for public artifact use
4. serializing a completed item into a public success row
5. serializing a failed/cancelled item into a public error row

Recommended names:

- `_public_batch_row_id(item)`
- `_public_batch_request_id(item)`
- `_sanitize_public_embedding_body(response_body)`
- `_serialize_completed_artifact_row(item)`
- `_serialize_failed_artifact_row(item)`

### Phase 3: Remove inline row construction from finalization

Replace the current direct `json.dumps(...)` calls in:

- `src/batch/worker.py`

Current paths to update:

- `_iter_output_lines()`
- `_iter_error_lines()`

Both should delegate to the serializer helpers so the emitted JSONL lines share one source of truth.

### Phase 4: Sanitize public success payloads

Update public success serialization so it:

1. uses existing `item.response_body` as `response.body`
2. removes `_provider`
3. preserves:
   - `object`
   - `data`
   - `model`
   - `usage`
4. preserves `data[0].object == "embedding"` and `data[0].index == 0`

This phase should not change internal completion accounting, pricing, or outbox payloads.

### Phase 5: Normalize error/cancel rows

Update public error serialization so:

1. the top-level row always includes `id`, `custom_id`, `response`, and `error`
2. `response` is `null` for current failed/cancelled items
3. `error` uses the stored `item.error_body` when present
4. a fallback normalized error object is emitted if `item.error_body` is absent

Do not block this phase on persisting upstream HTTP failure response bodies.

### Phase 6: Add focused regression tests

Add tests that assert exact artifact-row shape, not just partial fields.

Required coverage:

1. single-item success row is wrapped in the OpenAI Batch envelope
2. microbatched success row is wrapped in the same envelope for each item
3. success row contains `response.body` and not raw payload directly under `response`
4. success row contains `error: null`
5. success row does not contain `_provider` in the public artifact body
6. failed row contains `response: null` and top-level `error`
7. cancelled row uses the same envelope

Recommended test touchpoints:

- `tests/test_batch_worker.py`
- any artifact finalization tests already covering `write_lines_stream`

### Phase 7: Verify file-serving compatibility

Run a focused regression pass to confirm:

1. output artifacts still download through `GET /v1/files/{file_id}/content`
2. content type remains `application/jsonl`
3. only the row schema changed

## File-Level Change Set

Expected files to touch:

- `src/batch/worker.py`
- `tests/test_batch_worker.py`

Possible additional files if helper extraction is cleaner:

- `src/batch/models.py`
- `src/batch/repositories/mappers.py`
- `tests/test_batch_repository.py`

Files that should probably not change for this fix:

- `src/api/v1/endpoints/files.py`
- `src/batch/repository.py`
- DB migration files

## Acceptance Criteria

The fix is complete when all of the following are true:

1. Completed `/v1/embeddings` batch artifact rows are OpenAI Batch-compatible.
2. The embedding payload is nested under `response.body`.
3. Each emitted row has top-level `id`, `custom_id`, `response`, and `error`.
4. Successful rows set `error` to `null`.
5. Failed/cancelled rows set `response` to `null` and populate `error`.
6. Public artifact payloads do not expose `_provider`.
7. Focused tests lock the contract for both single-item and microbatched execution.

## Validation Checklist

- [ ] Serializer helpers added and used by finalization.
- [ ] Success rows emit `response.status_code = 200`.
- [ ] Success rows emit synthetic `response.request_id`.
- [ ] Success rows place the embedding payload under `response.body`.
- [ ] Error rows emit top-level `error`.
- [ ] `_provider` removed from public artifact output.
- [ ] Focused worker/finalization tests pass.

## Notes

- Keep the patch surgical. This issue does not justify a repository-wide batch refactor.
- Prefer compatibility at the artifact boundary over introducing new persistence fields unless tests prove they are necessary.
- If richer HTTP failure envelopes are desired later, capture that as a follow-up issue after the success-path compatibility contract is shipped.
