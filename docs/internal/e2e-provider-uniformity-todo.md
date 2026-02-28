# E2E Provider Uniformity Test TODO

## Scope
- Validate end-to-end behavior across OpenAI, Anthropic, and Groq deployments.
- Validate text endpoint parity: `/v1/chat/completions`, `/v1/completions`, `/v1/responses`.
- Validate guardrails, limits, budget, cache scope, fallback attribution, and usage/spend logs.

## Checklist

### Phase A — Environment & Baseline
- [x] Confirm `db` and `redis` are running.
- [x] Confirm backend is reachable and healthy.
- [x] Run migrations if needed.
- [x] Verify admin auth (master key) works.

### Phase B — Provider Model Setup
- [x] Register OpenAI deployment via admin API.
- [x] Register Anthropic deployment via admin API.
- [x] Register Groq deployment via admin API.
- [x] Verify models are listed in `/v1/models`.

### Phase C — Tenant & Key Setup
- [x] Create org.
- [x] Create team under org.
- [x] Create user under team.
- [x] Create scoped API key for user/team.

### Phase D — Text Endpoint E2E (Per Provider)
- [x] `/v1/chat/completions` non-stream succeeds.
- [x] `/v1/completions` non-stream succeeds.
- [x] `/v1/responses` non-stream succeeds.
- [x] `/v1/chat/completions` stream succeeds with `[DONE]`.

### Phase E — Policy/Control E2E
- [x] Validate rate-limit denial path.
- [x] Validate budget denial path (`429 budget_exceeded`).
- [x] Validate cache hit for same key + miss for different key.
- [x] Validate fallback attribution when primary fails.

### Phase F — Observability E2E
- [x] Verify logs include entries for all endpoint families.
- [x] Verify spend/usage records are present and coherent.
- [x] Verify cache-hit records include `cache_hit=true`.

## Run Log
- 2026-02-27: `db`/`redis` healthy via `docker compose ps`.
- 2026-02-27: Prisma sync run via `uv run prisma generate` + `DATABASE_URL=... uv run prisma db push`.
- 2026-02-27: Fixed startup blocker in `src/billing/budget.py` (`org` path selected `soft_budget` column that does not exist).
- 2026-02-27: Provider matrix passed on dedicated models for OpenAI, Groq, Anthropic across:
  - `/v1/chat/completions` (non-stream + stream)
  - `/v1/completions`
  - `/v1/responses`
- 2026-02-27: Budget denial validated (`429 budget_exceeded`) using key `max_budget=0`.
- 2026-02-27: Rate-limit denial validated (`429 rate_limit_error`, `key_tpm_exceeded`) using key `tpm_limit=1`.
- 2026-02-27: Fallback attribution validated with two deployments in one model group (`bad` priority 0 + `good` priority 1); request succeeded and `/ui/api/logs` recorded `api_base=https://api.openai.com/v1`.
- 2026-02-27: Cache-enabled E2E run completed on a dedicated runtime config (`cache_enabled=true`, `cache_backend=redis`):
  - key1 first call: `x-deltallm-cache-hit=false`
  - key1 second call: `x-deltallm-cache-hit=true`
  - key2 same payload: `x-deltallm-cache-hit=false`
  - `/ui/api/logs` recorded one `cache_hit=true` row for key1 and miss rows for key1/key2.
- 2026-02-27: UI analytics date-filter bug fixed in `src/ui/routes.py` by casting date placeholders to `::timestamp`; date-filtered `/ui/api/spend/summary` and `/ui/api/spend/report` now return successful responses (no DB type error).
