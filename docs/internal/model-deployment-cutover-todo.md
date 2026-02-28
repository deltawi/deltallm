# Model Deployment Cutover TODO

## Goal
Finalize migration so deployed models are sourced from `deltallm_modeldeployment` with explicit, safe cutover controls and no hidden fallback behavior.

## Scope
- Add explicit source mode controls for model loading.
- Keep bootstrap path for legacy environments during transition.
- Make fallback behavior intentional and observable.
- Avoid changing routing/provider behavior.

## Phase 1 — Config Controls
- [x] Add `general_settings.model_deployment_source` with modes: `hybrid`, `db_only`, `config_only`
- [x] Add `general_settings.model_deployment_bootstrap_from_config` boolean
- [x] Keep defaults safe for rollout (`hybrid`, bootstrap enabled)

## Phase 2 — Runtime Wiring
- [x] Apply source-mode selection in startup model loading
- [x] Apply same source-mode selection in hot-reload runtime refresh
- [x] Make bootstrap conditional on `model_deployment_bootstrap_from_config`
- [x] Add clear logs for selected source and bootstrap decisions

## Phase 3 — Safety Behavior
- [x] In `db_only`, fail fast when DB has no model deployments
- [x] In `hybrid`, fallback to config only when DB is empty/unavailable
- [x] In `config_only`, never read DB deployments

## Phase 4 — Tests
- [x] Unit test `hybrid` mode prefers DB and falls back to config
- [x] Unit test `db_only` fails when DB empty
- [x] Unit test `config_only` ignores DB content
- [x] Keep existing runtime hot-reload tests passing

## Phase 5 — Validation
- [x] Run focused test suite for new/changed paths
- [x] Record exact commands and results

### Validation Results
- `uv run pytest tests/services/test_model_deployments.py tests/db/test_model_deployment_repository.py tests/config/test_dynamic.py::test_model_hot_reload_manager_updates_runtime_registries tests/config/test_dynamic.py::test_model_hot_reload_manager_model_crud_refreshes_runtime_registry -q`
- Result: `9 passed`
- `python -m py_compile src/config.py src/services/model_deployments.py src/main.py src/config_runtime/models.py tests/services/test_model_deployments.py`
- Result: success

## Rollout Guidance
1. Start with `hybrid` + bootstrap enabled.
2. Verify DB has all deployments and fallback metrics/logs are zero.
3. Switch to `db_only` in staging, then production.
4. Disable bootstrap after confidence window.
5. Optional cleanup: remove legacy `model_list` usage in later PR.
