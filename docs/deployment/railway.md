# Railway Deployment

Use Railway when you want a managed evaluation deployment with DeltaLLM, PostgreSQL, and Redis created from one template.

The durable deployment artifact is a Railway Template. After the template is published, the repository README should link to the published template URL with Railway's deploy button.

```md
[![Deploy on Railway](https://railway.com/button.svg)](<RAILWAY_TEMPLATE_URL>)
```

Do not merge a README button with a placeholder URL.

Railway's deploy button uses the published template code:

```html
<a href="https://railway.com/deploy/<template-code>?utm_medium=integration&utm_source=template&utm_campaign=deltallm">
  <img src="https://railway.com/button.svg" alt="Deploy on Railway" height="40">
</a>
```

## Template Services

Create the template with three services:

| Service | Source | Notes |
|---------|--------|-------|
| `deltallm` | GitHub repo or published `deltallm/deltallm:<release>` image | Uses `deploy/railway/Dockerfile`, public HTTP networking, and `/health/readiness` |
| `Postgres` | Railway PostgreSQL service | Referenced by the app through `DATABASE_URL` |
| `Redis` | Railway Redis service | Referenced by the app through `REDIS_URL` and `DELTALLM_REDIS_URL` |

Prefer the GitHub repo source for the first template so Railway can track template updates from the repository. Pin a released branch or tag when publishing a stable template.

## App Service Settings

The repository includes `railway.toml` with the app service defaults:

```toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "deploy/railway/Dockerfile"

[deploy]
healthcheckPath = "/health/readiness"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

Do not hard-code `PORT`. Railway provides `PORT` at runtime, and `deploy/railway/Dockerfile` starts `uvicorn` with that value.

The Docker image default config path is intended for Docker Compose bind mounts. Railway must set:

```env
DELTALLM_CONFIG_PATH=/app/config.example.yaml
```

`config.example.yaml` is included in the image and is safe for a first-run Railway deployment because its secrets are environment-backed.

## Template Variables

Configure these variables on the `deltallm` service:

```env
DELTALLM_CONFIG_PATH=/app/config.example.yaml
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DELTALLM_REDIS_URL=${{Redis.REDIS_URL}}
DELTALLM_MASTER_KEY=sk-${{secret(32)}}A1
DELTALLM_SALT_KEY=${{secret(64)}}
PLATFORM_BOOTSTRAP_ADMIN_EMAIL=<user input>
PLATFORM_BOOTSTRAP_ADMIN_PASSWORD=<user input, 12+ characters>
OPENAI_API_KEY=<optional user input>
```

Variable notes:

- `DELTALLM_MASTER_KEY` must be at least 32 characters and include letters and digits. The `sk-${{secret(32)}}A1` shape satisfies the validator while keeping the value generated per deployment.
- `DELTALLM_SALT_KEY` must be unique and must not be `change-me`.
- `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` and `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD` create the initial browser-login admin account.
- Use an initial admin password with at least 12 characters so the first-login password-change flow can complete cleanly.
- `OPENAI_API_KEY` is optional. Supplying it makes the starter `gpt-4o-mini` deployment usable immediately.

## First Deploy

1. Open the published Railway Template URL.
2. Review the `deltallm`, `Postgres`, and `Redis` services.
3. Fill in the required admin variables.
4. Optionally set `OPENAI_API_KEY`.
5. Deploy the template.
6. Wait for the `deltallm` service healthcheck to pass.
7. Open the generated public domain for the `deltallm` service.

The first boot runs Prisma migrations before starting the API:

```bash
python -m src.prisma_bootstrap --schema ./prisma/schema.prisma --max-attempts 30 --sleep-seconds 2
```

The bootstrap retries database connectivity and then runs strict `prisma migrate deploy`.

## Post-Deploy Checks

Set `APP_URL` to the public Railway domain:

```bash
export APP_URL="https://your-deltallm-service.up.railway.app"
```

Check readiness:

```bash
curl "$APP_URL/health/readiness"
```

Expected response:

```json
{
  "status": "ok",
  "checks": {
    "redis": true,
    "database": true
  }
}
```

Log in to the Admin UI at `APP_URL` with:

- `PLATFORM_BOOTSTRAP_ADMIN_EMAIL`
- `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`

The bootstrap account may require a password change before you can use the rest of the Admin UI.

If `OPENAI_API_KEY` was supplied, verify the starter model:

```bash
export DELTALLM_MASTER_KEY="<copy from the deltallm service variables>"
curl "$APP_URL/v1/models" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY"
```

Send a chat request:

```bash
curl "$APP_URL/v1/chat/completions" \
  -H "Authorization: Bearer $DELTALLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [
      {"role": "user", "content": "Hello from Railway"}
    ]
  }'
```

If `OPENAI_API_KEY` was omitted, the app should still boot. Use the Admin UI to add a provider credential and update or create model deployments before sending gateway requests.

## Redeploy Behavior

Redeploys are designed to be idempotent:

- Prisma runs `migrate deploy`, so already-applied migrations are skipped.
- PostgreSQL data persists in the Railway PostgreSQL service.
- Redis state is external to the app service.
- Model bootstrap inserts the `config.example.yaml` model list only when the model deployment table is empty.

If a redeploy fails at migration time, inspect the `deltallm` deploy logs first. Do not use `prisma db push` against a Railway database; resolve migration history and rerun the deployment.

## Adding Providers And Models

After the app is running:

1. Open the Admin UI.
2. Go to the model deployment area.
3. Add or update provider credentials.
4. Create model deployments or update the starter `gpt-4o-mini` deployment.
5. Re-run `/v1/models` and a chat completion check.

For production-oriented deployments with dedicated batch workers, shared artifact storage, custom domains, and stricter operational controls, create a separate template rather than extending this evaluation template.

## Release Automation

The release workflow can publish or update the Railway template marketplace metadata after a GitHub Release is published.

Configure these repository settings before enabling the README deploy button:

| Setting | Type | Required | Notes |
|---------|------|----------|-------|
| `RAILWAY_API_TOKEN` | GitHub Actions secret | Yes | Railway account or workspace token with template publishing access |
| `RAILWAY_TEMPLATE_ID` | GitHub Actions variable | Yes | Stable template ID returned by the initial Railway template creation |
| `RAILWAY_TEMPLATE_WORKSPACE` | GitHub Actions variable | Optional | Workspace ID or name, useful when the token can access multiple workspaces |
| `RAILWAY_TEMPLATE_DEMO_PROJECT` | GitHub Actions variable | Optional | Public demo project ID to show on the template page |

The release workflow intentionally updates an existing reviewed template. It does not create a fresh template from a live smoke-test project on every release because that can accidentally copy test-only variables, source settings, or one-off infrastructure state.

Before the first automated publish:

1. Create the Railway template from a clean project whose `deltallm` service is connected to the public GitHub repository.
2. Confirm the template has the service graph and variables documented above.
3. Store the returned template ID in `RAILWAY_TEMPLATE_ID`.
4. Add `RAILWAY_API_TOKEN` as a GitHub Actions secret.
5. Run the next release workflow.
6. Copy the published template URL into the README button.
