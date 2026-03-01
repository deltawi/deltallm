# Embeddings Batch Plan & TODO

## Objective
Add OpenAI-style batch processing for **Embeddings only** with vLLM compatibility, while preserving existing sync endpoint behavior and shared governance (auth, rate limits, budgets, guardrails, routing, spend, metrics).

## Guardrails
- Scope is limited to `/v1/embeddings` batch jobs.
- Build reusable batch infrastructure, but enforce embeddings-only at API validation.
- Avoid duplicate business logic; reuse existing embeddings execution path.
- Keep Postgres as queue/status source of truth.
- Keep changes minimal and explicit; no unrelated refactors.

## Design Lock (must hold)
- API process (`uvicorn`) handles files/jobs/status endpoints.
- Worker process handles job execution from DB queue leases.
- Result artifacts stored in filesystem (dev) or object storage (prod via pluggable backend), referenced from DB.
- Batch pricing resolved at deployment/model level with explicit precedence.

## Pricing Rules (batch)
Resolution order per item:
1. `model_info.batch_input_cost_per_token` / `model_info.batch_output_cost_per_token`
2. `model_info.batch_price_multiplier` applied over sync pricing
3. Sync deployment pricing (`input_cost_per_token` / `output_cost_per_token`)
4. Default pricing map fallback

Accounting:
- `provider_cost`: upstream actual cost estimate
- `billed_cost`: tenant-billed cost (used for budget/spend enforcement)

## Retention Defaults
- Completed artifacts: 7 days
- Failed/cancelled artifacts: 14 days
- Metadata retention: configurable (default 30 days)
- Daily cleanup job removes expired artifacts then stale metadata.

## TODO

### Phase 1 — Impact Analysis
- [x] Map impacted modules and extension points.
- [x] Confirm no hidden coupling with current cache/metrics callbacks.
- [x] Document minimal file-level change set.

### Phase 1 Notes (Completed)
- Extension points confirmed:
  - Request/response API composition: `src/api/v1/router.py`, `src/api/v1/endpoints/__init__.py`
  - Existing embeddings execution lifecycle to reuse: `src/routers/embeddings.py`
  - Cost engine extension for batch tier: `src/billing/cost.py`
  - Deployment pricing schema source: `src/config.py` (`ModelInfo`)
  - DB access pattern (raw SQL repositories): `src/db/repositories.py`
  - App lifecycle wiring for services/workers: `src/main.py`
- Coupling check:
  - Cache middleware currently targets sync endpoints and does not provide batch lifecycle primitives (`src/cache/middleware.py`); no direct conflict.
  - Callback + guardrails + spend hooks are already centralized around request lifecycle and can be reused per batch item by invoking embeddings path/service.
- Minimal file-level change set (v1 implementation):
  - Update: `src/config.py`, `src/billing/cost.py`, `prisma/schema.prisma`, `src/db/repositories.py`, `src/main.py`, `src/api/v1/endpoints/__init__.py`, `src/api/v1/router.py`
  - Add: `src/batch/models.py`, `src/batch/repository.py`, `src/batch/service.py`, `src/batch/storage.py`, `src/batch/worker.py`, `src/api/v1/endpoints/files.py`, `src/api/v1/endpoints/batches.py`
  - Tests: new batch-focused unit/integration files under `tests/`

### Phase 2 — Data Layer
- [x] Add DB models/tables for `batch_file`, `batch_job`, `batch_item`.
- [x] Add repository methods for queue lease/claim/update/finalize.
- [x] Add migration path compatible with current Prisma workflow.

### Phase 3 — Pricing Extension
- [x] Extend deployment `model_info` schema for batch pricing fields.
- [x] Implement pricing resolver with explicit precedence.
- [x] Persist/emit both `provider_cost` and `billed_cost`.

### Phase 4 — API Surface (Embeddings Only)
- [x] Add `POST /v1/files` for JSONL upload.
- [x] Add `POST /v1/batches` with `endpoint=/v1/embeddings` enforcement.
- [x] Add `GET /v1/batches/{id}` and `GET /v1/batches`.
- [x] Add `POST /v1/batches/{id}/cancel`.
- [x] Add output/error artifact download endpoint(s).

### Phase 5 — Worker & Queue
- [x] Implement DB lease loop using `FOR UPDATE SKIP LOCKED`.
- [x] Execute items through shared embeddings lifecycle.
- [x] Implement retry/backoff for transient errors.
- [x] Implement terminal status transitions and progress counters.

### Phase 6 — Storage & Retention
- [x] Add local artifact storage backend.
- [x] Add pluggable object storage backend interface.
- [x] Add retention GC task and config knobs.

### Phase 7 — Tests
- [x] Unit tests (validator, lease logic, pricing precedence, status transitions).
- [ ] Integration tests (files+batches lifecycle, cancel, retry, finalize).
- [ ] E2E tests for OpenAI and vLLM embeddings batches.

### Phase 8 — Docs
- [x] Document batch endpoints and constraints under `docs/api/`.
- [x] Document operations (worker, retention, storage) under `docs/configuration/`.
- [ ] Update README matrix with implemented batch capabilities only.

### Phase 9 — Validation Run
- [ ] Run local E2E with vLLM CPU (`intfloat/multilingual-e5-large-instruct`) through gateway.
- [ ] Run small OpenAI embeddings batch validation through gateway.
- [x] Capture results and blockers explicitly.

### Phase 9 Notes (Current blockers)
- Official `vllm/vllm-openai:latest` container fails at startup in this macOS Docker environment with `RuntimeError: Failed to infer device type`.
- CPU-oriented fallback image `substratusai/vllm:main-cpu` starts process but fails runtime init due missing `libtbbmalloc.so.2`.
- Batch endpoints required DB sync; after `prisma db push`, `/v1/batches` is operational.
- OpenAI direct embeddings call with provided key succeeds (upstream connectivity/key validated).
- Gateway OpenAI chat proxy succeeds, but embedding deployment add/list behavior is inconsistent in current runtime (duplicate deployment ID in DB, not reflected in runtime registry listing), preventing reliable embeddings-batch E2E in this live instance without a controlled restart/reset.
