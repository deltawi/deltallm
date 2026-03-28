# Kubernetes Deployment

Use this guide to deploy DeltaLLM on Kubernetes with Helm.

There are two install paths:

- **Install from a released chart** if you want the simplest production or evaluation setup without cloning the repository.
- **Install from the repo** if you are developing the chart itself or testing local chart changes before release.

The rewritten chart supports three concrete deployment shapes:

- evaluation with bundled PostgreSQL and Redis
- standard production with external PostgreSQL and Redis
- high-availability production with multiple replicas, HPA, PDB, topology spread, ingress, and monitoring

## Prerequisites

- Kubernetes 1.24+
- Helm 3.10+
- `kubectl` access to the target cluster

## Option 1: Install From a Released Chart

Published releases are available from the public Helm repository at `https://deltawi.github.io/deltallm`.

Each release publishes three matching values files:

- `values-eval-<chart-version>.yaml`: self-contained quick-start with bundled PostgreSQL and Redis
- `values-production-<chart-version>.yaml`: HA-oriented production baseline for external PostgreSQL and Redis
- `values-<chart-version>.yaml`: raw base chart values

The bare chart does not provision PostgreSQL or Redis by default. For a first install, use the eval values file.

```bash
helm repo add deltallm https://deltawi.github.io/deltallm
helm repo update
```

Generate the required secrets first:

```bash
export DELTALLM_MASTER_KEY="$(python3 -c 'import secrets; print(\"sk-\" + secrets.token_hex(20) + \"A1\")')"
export DELTALLM_SALT_KEY="$(openssl rand -hex 32)"
```

Quick-start evaluation install:

```bash
helm install deltallm deltallm/deltallm \
  --version <chart-version> \
  --namespace deltallm \
  --create-namespace \
  -f https://deltawi.github.io/deltallm/values-eval-<chart-version>.yaml \
  --set secret.values.masterKey="$DELTALLM_MASTER_KEY" \
  --set secret.values.saltKey="$DELTALLM_SALT_KEY" \
  --set-string env[0].name=PLATFORM_BOOTSTRAP_ADMIN_EMAIL \
  --set-string env[0].value=admin@example.com \
  --set-string env[1].name=PLATFORM_BOOTSTRAP_ADMIN_PASSWORD \
  --set-string env[1].value='ChangeMe123!'
```

To use the Presidio-enabled image variant from the same release:

```bash
helm install deltallm deltallm/deltallm \
  --version <chart-version> \
  --namespace deltallm \
  --create-namespace \
  -f https://deltawi.github.io/deltallm/values-eval-<chart-version>.yaml \
  --set secret.values.masterKey="$DELTALLM_MASTER_KEY" \
  --set secret.values.saltKey="$DELTALLM_SALT_KEY" \
  --set-string env[0].name=PLATFORM_BOOTSTRAP_ADMIN_EMAIL \
  --set-string env[0].value=admin@example.com \
  --set-string env[1].name=PLATFORM_BOOTSTRAP_ADMIN_PASSWORD \
  --set-string env[1].value='ChangeMe123!' \
  --set image.tag=v<chart-version>-presidio
```

Use the latest GitHub Release version for `<chart-version>`. The exact pinned install commands for each release live in the release notes.

After install:

- `kubectl get pods -n deltallm` should show DeltaLLM plus bundled PostgreSQL and Redis pods
- use `admin@example.com` and the bootstrap password to sign in to the Admin UI
- use `DELTALLM_MASTER_KEY` for gateway and API requests

For production, do not use the eval overlay. Start from the released production overlay instead:

```bash
curl -fsSLo values-production.yaml \
  https://deltawi.github.io/deltallm/values-production-<chart-version>.yaml
```

Edit `values-production.yaml` to point at your external PostgreSQL and Redis secrets, then install:

- set `secret.existingSecret` to the secret that contains `master-key` and `salt-key`
- set `runtime.database.existingSecret.name` and `runtime.database.existingSecret.urlKey`
- set `runtime.redis.existingSecret.name` and `runtime.redis.existingSecret.urlKey`
- add any provider keys or platform credentials under `envFrom` or `env`

```bash
helm install deltallm deltallm/deltallm \
  --version <chart-version> \
  --namespace deltallm \
  --create-namespace \
  -f values-production.yaml \
  --set secret.existingSecret=deltallm-app-secrets
```

Use the eval overlay for the simplest first working install. Use the production overlay once you have external stateful services and secret-backed runtime configuration.

## Option 2: Install From the Repo

Use this path when you want to:

- inspect the chart locally
- test changes before opening a PR
- install directly from `./helm`

Clone the repository first:

```bash
git clone https://github.com/deltawi/deltallm.git
cd deltallm
```

### Fetch chart dependencies

The chart uses Bitnami PostgreSQL and Redis as optional subcharts.

```bash
helm dependency build ./helm
```

### Chart profiles

The chart now ships with three value layers:

- `helm/values.yaml`: safe baseline
- `helm/values-eval.yaml`: quick-start with bundled PostgreSQL and Redis
- `helm/values-production.yaml`: HA-oriented production defaults

By default, the app pod uses an init container to wait until the configured PostgreSQL and Redis endpoints accept TCP connections before DeltaLLM starts. This avoids the initial crash loop that can happen while bundled stateful dependencies are still coming up.

### Quick start from the repo

This path uses bundled PostgreSQL and Redis and generated control-plane secrets.

!!! warning "Generate the master key and salt key before you install"
    DeltaLLM will not start with placeholder values such as `change-me`.
    Generate both values first, then pass them into Helm.

    Copy and run:

    ```bash
    export DELTALLM_MASTER_KEY="$(python3 -c 'import secrets; print(\"sk-\" + secrets.token_hex(20) + \"A1\")')"
    export DELTALLM_SALT_KEY="$(openssl rand -hex 32)"
    ```

    `DELTALLM_MASTER_KEY` must be at least 32 characters long and include both letters and numbers.
    `DELTALLM_SALT_KEY` must be a real secret value and must not be `change-me`.

```bash
helm upgrade --install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  -f helm/values-eval.yaml \
  --set secret.values.masterKey="$DELTALLM_MASTER_KEY" \
  --set secret.values.saltKey="$DELTALLM_SALT_KEY"
```

Access the service with port-forwarding:

```bash
kubectl port-forward -n deltallm svc/deltallm 4000:4000
curl http://localhost:4000/health/liveliness
```

Open the admin UI at `http://localhost:4000`.

## Secret layout

For production, keep secrets out of Helm values.

Generate the secrets first if you have not already:

```bash
export DELTALLM_MASTER_KEY="$(python3 -c 'import secrets; print(\"sk-\" + secrets.token_hex(20) + \"A1\")')"
export DELTALLM_SALT_KEY="$(openssl rand -hex 32)"
```

Create one secret for `master-key` and `salt-key`:

```bash
kubectl create secret generic deltallm-app-secrets \
  --namespace deltallm \
  --from-literal=master-key="$DELTALLM_MASTER_KEY" \
  --from-literal=salt-key="$DELTALLM_SALT_KEY"
```

Create one secret for runtime environment variables:

```bash
kubectl create secret generic deltallm-runtime-secrets \
  --namespace deltallm \
  --from-literal=DATABASE_URL='postgresql://user:pass@postgres:5432/deltallm' \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=OPENAI_API_KEY='sk-...'
```

Then reference them from the chart:

```yaml
secret:
  existingSecret: deltallm-app-secrets

runtime:
  database:
    existingSecret:
      name: deltallm-runtime-secrets
      urlKey: DATABASE_URL
  redis:
    existingSecret:
      name: deltallm-runtime-secrets
      urlKey: REDIS_URL

envFrom:
  - secretRef:
      name: deltallm-runtime-secrets
```

The chart will not emit empty database or Redis env vars, so `envFrom` works cleanly for provider keys and platform integrations.

## Configuration patterns

### 1. Bundled PostgreSQL and Redis

Use the eval profile or enable both subcharts explicitly:

```yaml
postgresql:
  enabled: true
  image:
    tag: latest
  auth:
    username: deltallm
    password: change-this
    database: deltallm

redis:
  enabled: true
  image:
    tag: latest
  auth:
    enabled: true
    password: strong-redis-password
```

If bundled Redis auth is enabled, the chart will generate the correct authenticated URL for DeltaLLM.

If you need to tune or disable the startup wait behavior:

```yaml
dependencyWait:
  enabled: true
  timeoutSeconds: 180
  periodSeconds: 2
```

### 2. External PostgreSQL and Redis

Disable the bundled subcharts and reference external connection strings:

```yaml
postgresql:
  enabled: false

redis:
  enabled: false

secret:
  existingSecret: deltallm-app-secrets

runtime:
  database:
    existingSecret:
      name: deltallm-runtime-secrets
      urlKey: DATABASE_URL
  redis:
    existingSecret:
      name: deltallm-runtime-secrets
      urlKey: REDIS_URL

envFrom:
  - secretRef:
      name: deltallm-runtime-secrets
```

### 3. Provider credentials and platform settings

Use `env` and `envFrom` directly:

```yaml
envFrom:
  - secretRef:
      name: deltallm-runtime-secrets

env:
  - name: PLATFORM_BOOTSTRAP_ADMIN_EMAIL
    value: admin@example.com
```

This covers provider API keys, bootstrap admin credentials, SSO client credentials, JWT settings, and any other runtime env.

### 4. Model bootstrap from config

You can still seed deployments from `config.model_list`:

```yaml
config:
  model_list:
    - model_name: gpt-4o
      deltallm_params:
        model: openai/gpt-4o
        api_key: os.environ/OPENAI_API_KEY
        api_base: https://api.openai.com/v1
        timeout: 300
      model_info:
        mode: chat
  general_settings:
    model_deployment_source: hybrid
```

## Service and ingress

Ingress is disabled by default.

```yaml
service:
  type: LoadBalancer
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-type: nlb

ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
  hosts:
    - host: llm-gateway.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: llm-gateway-tls
      hosts:
        - llm-gateway.example.com
```

## High availability

Use the production profile as the base:

```bash
helm upgrade --install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  -f helm/values-production.yaml \
  -f values-custom.yaml
```

`values-production.yaml` gives you:

- `replicaCount: 3`
- HPA enabled
- PDB enabled
- topology spread constraints
- soft anti-affinity
- bundled PostgreSQL and Redis disabled

A typical HA overlay looks like this:

```yaml
secret:
  existingSecret: deltallm-app-secrets

runtime:
  database:
    existingSecret:
      name: deltallm-runtime-secrets
      urlKey: DATABASE_URL
  redis:
    existingSecret:
      name: deltallm-runtime-secrets
      urlKey: REDIS_URL

envFrom:
  - secretRef:
      name: deltallm-runtime-secrets

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: llm-gateway.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: llm-gateway-tls
      hosts:
        - llm-gateway.example.com

prometheus:
  serviceMonitor:
    enabled: true
```

## Optional migration job

The current container image still bootstraps Prisma on startup by default.

The chart also exposes an optional `migrationJob` for teams that want a separate Kubernetes job for explicit migration control:

```yaml
migrationJob:
  enabled: true
  hook:
    enabled: true
```

Use that only if your rollout process is intentionally built around a separate migration step. If you want the application pods to stop using the image default bootstrap path, set `command` and `args` explicitly for the app container.

## S3 request logging

Use workload identity or an existing secret.

### Workload identity

```yaml
serviceAccount:
  create: true
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/deltallm-s3-role
  automountServiceAccountToken: true

s3:
  enabled: true
  bucket: company-deltallm-logs
  region: us-east-1
  compression: gzip
```

### Existing AWS credentials secret

```yaml
s3:
  enabled: true
  bucket: company-deltallm-logs
  region: us-east-1
  existingSecret:
    name: deltallm-aws-creds
    accessKeyIdKey: aws-access-key-id
    secretAccessKeyKey: aws-secret-access-key
```

## Optional hardening features

The chart includes:

- `podDisruptionBudget`
- `topologySpreadConstraints`
- `affinity`
- `networkPolicy`
- `serviceAccount.automountServiceAccountToken`
- `startupProbe`, `readinessProbe`, and `livenessProbe`
- config and generated-secret checksum rollouts

If you enable `networkPolicy`, define ingress and egress rules that match your cluster and ingress-controller topology.

## Validation

Lint the chart before deploying:

```bash
helm lint ./helm -f helm/values-eval.yaml \
  --set secret.values.masterKey=StrongMasterKey2026SecureValue99 \
  --set secret.values.saltKey=unique-salt-2026

helm lint ./helm -f helm/values-production.yaml \
  --set secret.existingSecret=deltallm-app-secrets \
  --set runtime.database.existingSecret.name=deltallm-runtime-secrets \
  --set runtime.redis.existingSecret.name=deltallm-runtime-secrets \
  --set ingress.enabled=true \
  --set 'ingress.hosts[0].host=llm-gateway.example.com' \
  --set 'ingress.hosts[0].paths[0].path=/' \
  --set 'ingress.hosts[0].paths[0].pathType=Prefix'
```

If subchart dependencies are not present locally yet, run:

```bash
helm dependency build ./helm
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pod exits during startup | Missing `master-key` or `salt-key` | Set `secret.values.*` or `secret.existingSecret` |
| App cannot connect to PostgreSQL | Wrong external DB secret or bundled PostgreSQL disabled | Check `runtime.database.*` and subchart settings |
| App cannot connect to Redis | Wrong Redis URL or missing Redis auth password | Check `runtime.redis.*` or bundled `redis.auth.password` |
| Provider calls fail immediately | Missing provider env vars | Add them via `envFrom` / `env` |
| Config change did not roll pods | External secret changed outside Helm | Restart the deployment or rotate through your secret operator |
| Migration job fails | DB not reachable or migration command not appropriate | Inspect `kubectl logs job/<release>-migrate` and adjust `migrationJob.args` |
