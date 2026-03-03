# Deployment

DeltaLLM can be deployed in multiple ways depending on your infrastructure.

| Method | Best For |
|--------|----------|
| [Docker](docker.md) | Single-instance and small team deployments |
| [Kubernetes](kubernetes.md) | Large-scale, multi-instance production setups |

## Requirements

All deployment methods require:

- **PostgreSQL** database for storing keys, teams, users, and spend data
- **Redis** (recommended) for rate limiting, caching, distributed config propagation, and deployment state coordination
- A **master key** (min 32 characters) for admin authentication
- A **salt key** (unique per deployment) for API key hashing

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

DeltaLLM listens on port 4000 by default. The backend serves both the API and the built frontend from a single process.

### Database Migrations

The database schema is migrated automatically on startup via Prisma. No manual migration step is needed.

### Health Checks

Use the built-in health endpoints for load balancer and orchestrator probes:

- **Liveness:** `GET /health/liveliness` — Returns OK if the process is running
- **Readiness:** `GET /health/readiness` — Returns OK if PostgreSQL and Redis are reachable
