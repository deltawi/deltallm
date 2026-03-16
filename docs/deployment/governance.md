# Governance Rollout

This guide covers the operator workflow for rolling out scoped asset governance in a running gateway.

## What Is Runtime-Authoritative

Two runtime control planes are now authoritative:

- **Callable-target governance** for model names and route-group keys
- **MCP governance** for MCP server visibility and tool execution

Both are enforced from in-memory snapshots on each gateway instance. Admin writes update the local snapshot immediately and publish a Redis invalidation event so other instances reload in the background.

## Multi-Instance Behavior

DeltaLLM does **not** read Redis on the request path for governance checks.

Instead:

1. an admin write updates the database
2. the local instance reloads the affected governance snapshot immediately
3. the instance publishes a governance invalidation event
4. other instances coalesce nearby invalidations and reload their local snapshot

This keeps request-time latency flat while still converging a horizontally scaled deployment quickly after changes.

## Callable-Target Rollout

Use callable-target migration to move orgs off legacy team/key/user model arrays.

### Report

```bash
curl -sS "$BASE/ui/api/callable-target-migration/report" \
  -H "Authorization: Bearer $MASTER_KEY"
```

### Backfill

```bash
curl -sS -X POST "$BASE/ui/api/callable-target-migration/backfill" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"rollout_states":["needs_org_bootstrap","needs_scope_backfill"]}'
```

Target state:

- organization callable-target grants exist
- team, API key, and user `restrict` policies exist where direct bindings exist
- rollout state is `ready_for_enforce`

## MCP Rollout

MCP has the same end-state model:

- organization bindings are the ceiling
- team, API key, and user policies narrow access with `restrict`

### Report

```bash
curl -sS "$BASE/ui/api/mcp-migration/report" \
  -H "Authorization: Bearer $MASTER_KEY"
```

### Backfill

```bash
curl -sS -X POST "$BASE/ui/api/mcp-migration/backfill" \
  -H "Authorization: Bearer $MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"rollout_states":["needs_org_bootstrap","needs_scope_backfill"]}'
```

Target state:

- org-level MCP bindings exist for every server the org currently relies on
- child scopes with direct bindings have `restrict` policies
- rollout state is `ready_for_enforce`

## Verification Checklist

After rollout:

1. `GET /v1/models` for representative keys returns only expected callable targets
2. MCP server visibility matches the intended organization/team/key/user chain
3. admin asset previews match runtime behavior
4. no org remains in a migration state that still relies on compatibility fallback

## Recommended Production Sequence

1. run the callable-target migration report
2. backfill callable-target governance
3. verify orgs are `ready_for_enforce`
4. run the MCP migration report
5. backfill MCP governance
6. verify orgs are `ready_for_enforce`
7. sample `/v1/models` and MCP requests with real scoped keys
