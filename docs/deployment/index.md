# Deployment

Use this section when you are moving from local evaluation to a repeatable environment.

## Choose a Deployment Path

| Path | Best for | Start here |
|------|----------|------------|
| Docker Compose | Single instance, demos, small teams, simple self-hosting | [Docker](docker.md) |
| Kubernetes | Multi-instance production, autoscaling, managed infrastructure | [Kubernetes](kubernetes.md) |

## Quick Path to Success

1. Choose Docker if you want the fastest production-style setup
2. Choose Kubernetes if you need replicas, ingress, and cluster-native operations
3. Generate a valid `DELTALLM_MASTER_KEY` and `DELTALLM_SALT_KEY`
4. Keep secrets in environment variables, not in `config.yaml`
5. Verify `/health/liveliness` and `/health/readiness` after startup

## Shared Requirements

All deployment methods rely on the same core services:

- PostgreSQL for persistent runtime data such as keys, accounts, spend logs, and model records
- Redis for distributed coordination, rate limiting, cache sharing, and runtime state
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

### Expect Schema Setup on Startup

The application runs Prisma schema setup automatically during container startup. You do not need a separate manual migration step for the default deployment paths documented here.

## Next Steps

- [Docker deployment guide](docker.md)
- [Kubernetes deployment guide](kubernetes.md)
- [Observability](../features/observability.md)
