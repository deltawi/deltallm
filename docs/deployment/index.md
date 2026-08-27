# Deployment

Use this section when you are moving from local evaluation to a repeatable environment.

## Choose a Deployment Path

| Path | Best for | Start here |
|------|----------|------------|
| Railway | Managed one-click evaluation stack with hosted PostgreSQL and Redis | [Railway](railway.md) |
| Docker Compose | Local evaluation, demos, and single-host testing | [Docker](docker.md) |
| Kubernetes | Multi-instance production, autoscaling, managed infrastructure | [Kubernetes](kubernetes.md) |
| Batch production setup | Async embedding/chat workloads with dedicated workers and shared storage | [Batch API & Production Setup](../features/batching.md#recommended-production-setup) |
| Batch webhook rollout | Optional terminal callbacks, split workers, alerts, rollout, and rollback | [Batch Webhook Rollout](batch-webhook-rollout.md) |
| Upstream HTTP tuning | Production provider concurrency, streaming, and egress capacity planning | [Upstream HTTP Tuning](upstream-http.md) |
| Scoped usage rollout | Safe activation of team and personal usage reporting | [Scoped Usage Reporting](usage-reporting-v2.md) |
| Durable telemetry rollout | Bounded spend, audit, and prompt-render ingestion with isolated database capacity | [Durable Telemetry Ingestion](telemetry-ingestion-rollout.md) |

## Quick Path to Success

1. Choose Railway if you want the fastest managed evaluation deployment
2. Choose Docker if you want a local or single-host evaluation
3. Choose Kubernetes if you need replicas, ingress, and cluster-native operations
4. Generate a valid `DELTALLM_MASTER_KEY` and `DELTALLM_SALT_KEY`
5. Keep secrets in environment variables, not in `config.yaml`
6. Verify `/health/liveliness` and, from a trusted operator network, `/health/readiness`

## Shared Requirements

All deployment methods rely on the same core services:

- PostgreSQL for persistent runtime data such as keys, accounts, spend logs, and model records
- Redis for distributed coordination, rate limiting, cache sharing, and runtime state
- Explicit upstream HTTP connection limits for predictable provider concurrency
- A master key for admin access
- A salt key for API key hashing

## Shared Best Practices

### Store Secrets in Environment Variables

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  salt_key: os.environ/DELTALLM_SALT_KEY
  database_url: os.environ/DATABASE_URL
  redis_url: os.environ/REDIS_URL
```

### Use the Built-In Health Endpoints

- `GET /health/liveliness` for process liveness
- `GET /health/readiness` for dependency readiness
- `GET /metrics` for Prometheus scraping

Readiness, deployment diagnostics, fallback events, and metrics currently have no application
authentication. Keep them on a private operational path. See [Health, diagnostics, and
metrics](../api/health.md).

### Coordinate Schema Migrations

The default image runs strict Prisma migrations before Uvicorn. That is convenient for
single-container evaluation, but it is not the multi-replica production contract. Production
delivery must run one migration job for the pinned release, wait for success, and only then roll
application and worker replicas with the image bootstrap command overridden. See [Database
migrations](database-migrations.md).

## Next Steps

- [Docker deployment guide](docker.md)
- [Railway deployment guide](railway.md)
- [Kubernetes deployment guide](kubernetes.md)
- [Production checklist](production-checklist.md)
- [Database migrations](database-migrations.md)
- [Upgrades and rollbacks](upgrade-and-rollback.md)
- [Backup and restore](backup-and-restore.md)
- [Incident runbooks](incident-runbooks.md)
- [Security hardening](../security/hardening.md)
- [Upstream HTTP tuning](upstream-http.md)
- [Scoped usage reporting rollout](usage-reporting-v2.md)
- [Durable telemetry ingestion rollout](telemetry-ingestion-rollout.md)
- [Batch webhook rollout](batch-webhook-rollout.md)
- [Observability](../features/observability.md)
