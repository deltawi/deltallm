# Model Deployment Storage Migration Plan

## Objective
Move deployed model persistence from `deltallm_config.proxy_config.model_list` (JSON blob) to a first-class relational table, while preserving runtime behavior and avoiding regressions.

## Current State (Verified)
- Startup/runtime model registry is built from `AppConfig.model_list`.
- Dynamic config persists one JSON payload under `deltallm_config(config_name='proxy_config')`.
- `/ui/api/models` CRUD currently mutates `model_list` via `ModelHotReloadManager`.
- Routing/failover/health paths consume `app.state.model_registry` and should remain unchanged.

## Design Decisions
- Canonical storage for deployments: new DB table `deltallm_modeldeployment`.
- Canonical storage for non-model settings: keep `deltallm_config` JSON blob.
- `deployment_id` is immutable identifier.
- During rollout, enable compatibility fallback read from `model_list` when table is empty.
- Use one shared builder to produce runtime registry (remove duplicate builders).

## Impact Analysis
### Code Areas Impacted
- Schema + migrations: `prisma/schema.prisma` (+ migration SQL)
- Runtime config: `src/main.py`, `src/config_runtime/models.py`, `src/config_runtime/dynamic.py`
- Admin/UI model CRUD path: `src/ui/routes.py`
- New data-access layer: `src/db/repositories.py` (or dedicated repository module)
- Tests: `tests/config/test_dynamic.py` and new repository/runtime parity tests

### Risk Areas
- Startup model loading regression
- Hot-reload regression after create/update/delete model
- Backward compatibility with existing `proxy_config.model_list`
- Concurrent updates creating duplicate deployment IDs

### Mitigations
- Keep runtime contract (`app.state.model_registry`) unchanged
- Add unique constraint on `deployment_id`
- Keep fallback read behavior until cutover complete
- Add focused tests for startup load + CRUD + parity

## Implementation Phases

## Phase 1 — Schema + Repository
- [x] Add `DeltaLLM_ModelDeployment` model in Prisma schema
- [x] Add indexes and uniqueness constraints (`deployment_id` PK, index `model_name`)
- [x] Add repository methods: `list_all`, `get`, `create`, `update`, `delete`
- [x] Ensure repository output shape maps cleanly to existing runtime registry format

## Phase 2 — Runtime Read Path
- [x] Add shared helper/service to build runtime model registry from deployment rows
- [x] Update startup to load model deployments from table first
- [x] If table empty, fallback to `cfg.model_list` (compat mode)
- [x] Remove duplicate builder logic between `src/main.py` and `src/config_runtime/models.py`

## Phase 3 — Runtime Write Path
- [x] Update `/ui/api/models` create/update/delete to use repository-backed writes
- [x] Preserve current API responses and status codes
- [x] Trigger runtime registry rebuild/reload via existing hot-reload path
- [x] Ensure no full-config writes are required for model CRUD

## Phase 4 — Migration Compatibility
- [x] Add one-time backfill utility path (or startup bootstrap step) from `model_list` to table when table empty
- [x] Keep fallback read for one transition window
- [x] Add observability logs for source-of-truth used (table vs fallback)

## Phase 5 — Validation
- [x] Unit tests for repository CRUD + conflict behavior
- [x] Runtime parity tests (same model inputs => same `model_registry` contract)
- [x] Integration-style test for model CRUD triggering runtime availability
- [x] Regression test for fallback load from `model_list` when table empty
- [x] Run focused test suite and record outcomes

## Cutover/Follow-up (Post-Implementation)
- [ ] Remove fallback read path once production data is fully migrated
- [ ] Stop storing `model_list` in dynamic config payload
- [ ] Add documentation note for operators in `docs/internal/`

## Open Notes
- Migration keeps compatibility-first posture to minimize blast radius.
- No changes to routing algorithm logic are needed as long as `model_registry` shape remains stable.
- Focused tests run:
  - `uv run pytest tests/services/test_model_deployments.py tests/db/test_model_deployment_repository.py tests/config/test_dynamic.py::test_model_hot_reload_manager_updates_runtime_registries tests/config/test_dynamic.py::test_model_hot_reload_manager_model_crud_refreshes_runtime_registry -q`
  - Result: `7 passed`
