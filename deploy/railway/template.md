# Deploy and Host DeltaLLM on Railway

## About Hosting

DeltaLLM is a self-hosted AI gateway with an OpenAI-compatible API, model routing, virtual API keys, spend tracking, guardrails, and an Admin UI.

This Railway template provisions:

- a `deltallm` web service
- managed PostgreSQL
- managed Redis
- public HTTP networking for the Admin UI and gateway API
- a `/health/readiness` healthcheck

## Why Deploy

Use this template when you want a managed DeltaLLM evaluation deployment without operating your own database, Redis instance, or container host. Railway provisions the app and backing services together, wires internal service URLs through reference variables, and keeps PostgreSQL and Redis data on managed volumes.

## Common Use Cases

- Evaluate DeltaLLM with the Admin UI and OpenAI-compatible API.
- Route requests through virtual API keys and model deployments.
- Test spend tracking, guardrails, and provider credential management.
- Run a small managed gateway before moving to a production Kubernetes or dedicated-worker deployment.

## Dependencies for DeltaLLM Hosting

### Deployment Dependencies

This template deploys the public `deltallm/deltallm:latest` image with managed PostgreSQL and Redis services.

PostgreSQL and Redis variables are preconfigured with Railway's standard defaults. Change them only if you intentionally want custom database or cache settings.

Set these when deploying the template:

| Variable | Purpose |
|----------|---------|
| `DELTALLM_MASTER_KEY` | Initial gateway credential for API calls |
| `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` | Initial Admin UI login email |
| `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD` | Initial Admin UI password |

`DELTALLM_MASTER_KEY` must be at least 32 characters and include letters and digits. The template generates `DELTALLM_SALT_KEY` per deployment and sets `DELTALLM_CONFIG_PATH=/app/config.example.yaml` so DeltaLLM loads the bundled Railway-safe config file.

## After Deployment

1. Wait for the `deltallm` service healthcheck to pass.
2. Open the generated Railway domain.
3. Log in with `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` and `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`.
4. Add provider credentials and model deployments in the Admin UI.

Read the full deployment guide in the DeltaLLM docs:

https://deltallm.readthedocs.io/en/latest/deployment/railway/
