# Deploy DeltaLLM on Railway

DeltaLLM is a self-hosted AI gateway with an OpenAI-compatible API, model routing, virtual API keys, spend tracking, guardrails, and an Admin UI.

This Railway template provisions:

- a `deltallm` web service
- managed PostgreSQL
- managed Redis
- public HTTP networking for the Admin UI and gateway API
- a `/health/readiness` healthcheck

## Required Variables

Set these when deploying the template:

| Variable | Purpose |
|----------|---------|
| `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` | Initial Admin UI login email |
| `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD` | Initial Admin UI password |

The template generates `DELTALLM_MASTER_KEY` and `DELTALLM_SALT_KEY` per deployment. `OPENAI_API_KEY` is optional; when provided, the starter `gpt-4o-mini` deployment can be used immediately.

## After Deployment

1. Wait for the `deltallm` service healthcheck to pass.
2. Open the generated Railway domain.
3. Log in with `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` and `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`.
4. Add provider credentials and model deployments in the Admin UI if you did not set `OPENAI_API_KEY`.

Read the full deployment guide in the DeltaLLM docs:

https://deltallm.readthedocs.io/en/latest/deployment/railway/
