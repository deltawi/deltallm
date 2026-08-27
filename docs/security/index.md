# Security Model

DeltaLLM is a gateway and control plane that handles provider credentials, tenant identities,
prompts, model responses, and usage metadata. Secure operation depends on separating its public
data-plane endpoints from its administrative and operational surfaces.

## Trust boundaries

| Boundary | Typical routes | Caller | Required control |
| --- | --- | --- | --- |
| Data plane | `/v1/*`, `/mcp/*` | Applications and approved clients | DeltaLLM API key or configured JWT, scoped to the intended tenant and models |
| Control plane | `/ui/api/*`, `/auth/*`, Admin UI | Platform and tenant administrators | Browser session, master credential, or role-checked administrative bearer auth |
| Operations | `/health/*`, `/metrics` | Orchestrator and monitoring systems | Private network or authenticated infrastructure proxy |
| Dependencies | PostgreSQL, Redis, providers, callbacks, MCP servers | DeltaLLM workloads | Network policy, TLS where supported, and least-privilege credentials |

See [Tenancy and access](../concepts/tenancy-and-access.md) for the authorization model and [API
conventions](../api/conventions.md) for route-level authentication behavior.

## Sensitive assets

- `DELTALLM_MASTER_KEY` is a bootstrap/platform credential. Limit its use, store it in a secret
  manager, and issue narrower virtual keys for applications.
- `DELTALLM_SALT_KEY` protects stored key material. Treat loss and rotation as a planned data
  migration, not an ordinary configuration change.
- Provider, SSO, email, object-storage, PostgreSQL, and Redis credentials must enter through a
  secret store or environment injection—not committed YAML or Helm values.
- Prompt and response content can appear in provider calls, callbacks, audit metadata, or
  observability sinks depending on configuration. Apply data classification and retention rules
  to every sink, not only PostgreSQL.

## Current deployment-relevant limitations

The documentation describes the current implementation, including these boundaries:

- `/health/*` and `/metrics` do not enforce application authentication. Network-isolate detailed
  health, diagnostics, and metrics.
- The default Docker image command runs database migration bootstrap before Uvicorn. Production
  replicas must use a coordinated pre-rollout migration and an explicit application command.
- The current Dockerfile runtime runs as root, and base Helm values use mutable image tags in some
  evaluation defaults. Production operators must pin images and compensate with workload
  isolation while a non-root image contract is not available.
- The application does not install CORS or trusted-host middleware by default. Enforce allowed
  hosts, origins, TLS, request-size limits, and proxy-header trust at the edge.

These are deployment constraints, not controls supplied by the documentation site.

## Security workflow

1. Review [Production hardening](hardening.md) before exposing a deployment.
2. Complete the [production checklist](../deployment/production-checklist.md).
3. Exercise [backup and restore](../deployment/backup-and-restore.md) and the [incident
   runbooks](../deployment/incident-runbooks.md).
4. Report suspected vulnerabilities through the [private reporting process](vulnerability-reporting.md).
