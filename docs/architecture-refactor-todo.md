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
- [x] Update `src/ui/__init__.py` to expose admin router via compatibility alias

## Phase 4: Validation
- [x] Compile check changed source files
- [x] Confirm no endpoint path regressions in route registration

## Notes
- Scope of this refactor is structural organization; no endpoint behavior changes intended.
- Existing `src/ui/routes.py` remains source of UI/admin endpoint definitions in this pass.
- Runtime import validation requiring third-party deps could not run in this environment (`httpx` missing on host Python).
