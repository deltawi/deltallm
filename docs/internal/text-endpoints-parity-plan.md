# Text Endpoints Parity Plan (`/v1/chat/completions`, `/v1/completions`, `/v1/responses`)

## Scope

Implement OpenAI-compatible text generation endpoint parity without duplicating core request lifecycle code.

## Principles

- One shared orchestration pipeline for auth-context request execution, failover, metrics, spend, callbacks, and guardrails.
- Thin endpoint adapters for request/response schema translation.
- Explicit compatibility behavior for partially supported `responses` features.
- No behavior regression for existing `/v1/chat/completions`.

## Phased TODO

### Phase 0 — Planning + Baseline

- [x] Capture implementation plan in `docs/internal/`.
- [x] Keep this checklist updated during implementation.

### Phase 1 — Shared text generation pipeline

- [x] Extract shared non-stream + stream execution helpers from chat router.
- [x] Keep callback/guardrail/metrics/spend logic centralized.
- [x] Keep `chat` endpoint behavior unchanged via wrapper usage.

### Phase 2 — Request/response schemas and adapters

- [x] Add `CompletionsRequest` / `CompletionsResponse` models.
- [x] Add `ResponsesRequest` / `ResponsesResponse` models (compatible subset).
- [x] Add adapter functions between each endpoint schema and internal chat-shaped canonical form.

### Phase 3 — New endpoint routers

- [x] Add `POST /v1/completions` router.
- [x] Add `POST /v1/responses` router.
- [x] Ensure auth + rate-limit deps match `chat`.
- [x] Ensure stream and non-stream both supported.

### Phase 4 — Cross-cutting parity

- [x] Extend cache middleware endpoint allowlist for new routes.
- [x] Reuse shared usage/cost/metrics path for new routes.
- [x] Keep provider error mapping consistent.

### Phase 5 — Documentation + verification

- [x] Update `docs/api/proxy.md` to document new endpoints + compatibility notes.
- [x] Add tests for new endpoints (happy path + streaming + key error path).
- [x] Run targeted tests and record outcome.

Verification run:
- `uv run pytest -q tests/test_chat.py tests/test_text_endpoints.py tests/test_cache.py`
- Result: `15 passed`

## Open Questions / Decisions Log

- Initial implementation decision: support a clear, documented subset of OpenAI `responses` API by mapping to chat completions internally (`input` text + optional `instructions`; streamed output emitted as SSE text frames).
- Future extension path: expand `responses` item/tool semantics with dedicated upstream handling when needed.
