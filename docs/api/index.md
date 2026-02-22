# API Reference

DeltaLLM exposes two sets of APIs:

| API | Base Path | Purpose |
|-----|-----------|---------|
| [Proxy Endpoints](proxy.md) | `/v1/` | OpenAI-compatible LLM endpoints |
| [Admin Endpoints](admin.md) | `/ui/api/` | Gateway management and configuration |
| [Health & Metrics](health.md) | `/health`, `/metrics` | Monitoring and diagnostics |

## Authentication

All API requests require authentication via one of:

- **Bearer token** — `Authorization: Bearer <key>` (master key or virtual key)
- **Session cookie** — `deltallm_session` cookie (set by the login endpoint)

The header name for Bearer tokens is configurable via `deltallm_key_header_name` (default: `Authorization`).
