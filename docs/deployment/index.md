# Deployment

DeltaLLM can be deployed in multiple ways depending on your infrastructure.

| Method | Best For |
|--------|----------|
| [Docker](docker.md) | Most production deployments |
| [Kubernetes](kubernetes.md) | Large-scale, multi-instance setups |

## Requirements

All deployment methods require:

- **PostgreSQL** database for storing keys, teams, users, and spend data
- **Redis** for rate limiting, caching, and deployment state
- A `config.yaml` file with model and gateway configuration

## Production Considerations

### Environment Variables

Store all secrets as environment variables, not in config files:

```yaml
general_settings:
  master_key: os.environ/DELTALLM_MASTER_KEY
  salt_key: os.environ/DELTALLM_SALT_KEY
  database_url: os.environ/DATABASE_URL
```

### Port Configuration

DeltaLLM listens on port 5000 by default in production. The backend serves both the API and the built frontend from a single process.

### Database Migrations

Run Prisma migrations before starting:

```bash
prisma generate
prisma db push
```

### Health Checks

Use the built-in health endpoints for load balancer and orchestrator probes:

- Liveness: `GET /health/liveliness`
- Readiness: `GET /health/readiness`
