# Admin MCP Endpoints

This package contains the admin MCP HTTP endpoints that were previously
implemented in a single `mcp.py` module.

## Public Surface

- Keep `from src.api.admin.endpoints.mcp import router` working.
- [__init__.py](./__init__.py) should remain a thin compatibility layer.
- [router.py](./router.py) is the aggregation point for route modules.

## Module Ownership

- `routes/servers.py`: server CRUD, operations, capability refresh, health checks.
- `routes/bindings.py`: binding list, upsert, delete endpoints.
- `routes/scope_policies.py`: scope policy list, upsert, delete endpoints.
- `routes/tool_policies.py`: tool policy list, upsert, delete endpoints.
- `routes/approvals.py`: approval request list and decision endpoints.
- `routes/migration.py`: migration report and backfill endpoints.
- `dependencies.py`: request-scoped accessors for repository, registry, probe, transport, and db services.
- `validators.py`: payload normalization and request validation helpers.
- `serializers.py`: response shaping helpers for MCP records.
- `scope_visibility.py`: auth-scope checks and scoped write guards.
- `sql_visibility.py`: shared SQL visibility clause builders.
- `loaders.py`: record loaders and scoped query helpers.
- `operations.py`: shared operational helpers such as capability refresh and tool filtering.
- `constants.py`: module-level constants shared across the package.

## Maintenance Rules

- Preserve route registration order in `router.py`. This keeps OpenAPI output and route dispatch behavior aligned with the pre-split module.
- Prefer putting shared logic in the helper modules instead of duplicating it across `routes/*`.
- Keep route modules focused on HTTP concerns: request parsing, permission checks, orchestration, and response payloads.
- Keep helper modules import-safe and narrow in responsibility to avoid recreating another monolith.
- When adding a new endpoint area, add a new route module and include it from `router.py` instead of growing an unrelated module.

## Import Guidance

- Other packages should import only the package `router` unless they have a clear internal maintenance reason to depend on package-private helpers.
- Cross-module imports inside this package should flow toward shared helpers, not sideways between unrelated route modules.
- Avoid importing from `routes/*` outside this package.
