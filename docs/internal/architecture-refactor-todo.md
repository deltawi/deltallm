# Architecture Refactor TODO

## Goals
- [x] Clarify separation between HTTP routes and internal routing domain logic
- [x] Provide stable, scalable API route aggregation structure
- [x] Preserve compatibility for existing imports and endpoints

## Phase 1: Route Aggregation
- [x] Add `src/api/v1/endpoints/*` wrappers for public endpoints
- [x] Add `src/api/v1/router.py` aggregator
- [x] Add `src/api/admin/router.py` aggregator

## Phase 2: Domain Namespace
- [x] Add `src/domain/routing` namespace as canonical domain-routing entry point
- [x] Keep compatibility with existing `src/router` imports

## Phase 3: App Wiring
- [x] Update `src/main.py` to include new aggregated routers
- [x] Update `src/ui/__init__.py` compatibility exports

## Phase 4: Full Admin Endpoint Split
- [x] Create `src/api/admin/endpoints/{keys,users,teams,organizations,guardrails,config}.py`
- [x] Move corresponding `/ui/api/*` admin handlers out of `src/ui/routes.py`
- [x] Add `src/api/admin/endpoints/common.py` shared helpers for admin modules
- [x] Keep `src/ui/routes.py` for remaining UI/static + non-split handlers
- [x] Keep route compatibility (same paths, methods, and payload shape)

## Phase 5: Validation
- [x] Compile check changed source files
- [ ] Re-test critical admin routes end-to-end against local stack

## Notes
- Scope of this refactor is structural organization; no endpoint behavior changes intended.
- `src/api/admin/router.py` now mounts split admin endpoints and then the legacy `src/ui/routes.py` router for remaining handlers.
