# API Reference

DeltaLLM exposes multiple API surfaces:

| API | Base path | Purpose |
|-----|-----------|---------|
| [Proxy Endpoints](proxy.md) | `/v1/` | OpenAI-compatible inference endpoints used by applications |
| [MCP Gateway & Tooling](mcp.md) | `/mcp`, `/v1/*`, `/ui/api/mcp-*` | Direct MCP access plus MCP-aware chat and admin workflows |
| [Admin Endpoints](admin.md) | `/ui/api/` and `/auth/` | Gateway management, runtime configuration, accounts, and operations |
| [Health & Metrics](health.md) | `/health` and `/metrics` | Monitoring and diagnostics |

## Start Here

Most developers should follow this order:

1. Make sure at least one model is available in `GET /v1/models`
2. Send a test request through [Proxy Endpoints](proxy.md)
3. If you want external tools, register and verify them through [MCP Gateway & Tooling](mcp.md)
4. Create scoped keys or manage runtime state through [Admin Endpoints](admin.md)
5. Use [Health & Metrics](health.md) for readiness and monitoring

For the first working `curl`, Python, and JavaScript examples, see [Quick Start](../getting-started/quickstart.md).

## Authentication

DeltaLLM supports two API auth patterns:

- Bearer token in `Authorization: Bearer <key>`
- Session cookie (`deltallm_session`) for authenticated Admin UI sessions

Bearer tokens can be:

- the platform master key
- a scoped virtual API key created through the admin API or UI

The bearer header name is configurable through `general_settings.deltallm_key_header_name`, but `Authorization` is the default and the path most clients should use.
