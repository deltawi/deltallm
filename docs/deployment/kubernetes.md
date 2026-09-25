# Kubernetes and Helm reference

Use this reference for Helm settings, production topology, and advanced Kubernetes behavior. For a
short installation path, start with [Deploy on Kubernetes](../guides/kubernetes-deployment.md).

The released chart supports three deployment shapes:

- evaluation with bundled PostgreSQL and Redis
- standard production with external PostgreSQL and Redis
- high-availability production with multiple replicas, HPA, PDB, topology spread, ingress, and monitoring

Production batch workloads can additionally split batch workers into a dedicated Deployment so UI/API/gateway pods do not execute batch work.

Upgrades introducing the router Redis v1 namespace require a one-time non-overlapping cutover. Read
the [router Redis v1 schema cutover](router-state-schema-cutover.md) before upgrading from an older
release; the chart intentionally blocks an ordinary rolling upgrade during this compatibility
window.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.10+
- `kubectl` access to the target cluster

## Install from a released chart

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

Create two Kubernetes Secrets with your approved secret-management process before installing:

- `deltallm-app-secrets` with `master-key` and `salt-key`
- `deltallm-bootstrap-admin` with `PLATFORM_BOOTSTRAP_ADMIN_EMAIL` and
  `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`

Use a unique administrator password. Do not pass secret values through Helm command arguments.

Quick-start evaluation install:

```bash
helm install deltallm deltallm/deltallm \
  --version <chart-version> \
  --namespace deltallm \
  --create-namespace \
  -f https://deltawi.github.io/deltallm/values-eval-<chart-version>.yaml \
  --set secret.existingSecret=deltallm-app-secrets \
  --set-string envFrom[0].secretRef.name=deltallm-bootstrap-admin
```

To use the Presidio-enabled image variant from the same release:

```bash
helm install deltallm deltallm/deltallm \
  --version <chart-version> \
  --namespace deltallm \
  --create-namespace \
  -f https://deltawi.github.io/deltallm/values-eval-<chart-version>.yaml \
  --set secret.existingSecret=deltallm-app-secrets \
  --set-string envFrom[0].secretRef.name=deltallm-bootstrap-admin \
  --set image.tag=v<chart-version>-presidio
```

Use the latest GitHub Release version for `<chart-version>`. The exact pinned install commands for each release live in the release notes.

After install:

- `kubectl get pods -n deltallm` should show DeltaLLM plus bundled PostgreSQL and Redis pods
- use the email and password stored in `deltallm-bootstrap-admin` to sign in to the Admin UI
- retrieve the master key through your approved secret-access process for initial API checks

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

## Secret layout

For production, keep secrets out of Helm values and command arguments. Create them through your
approved secret manager, GitOps controller, or another process that does not expose values in
shell history or process listings.

Use one secret for `master-key` and `salt-key`. Use a separate runtime secret for `DATABASE_URL`,
`REDIS_URL`, provider keys, and integration credentials. The following values file contains only
secret names and key names:

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

Use the released evaluation profile. It enables both subcharts and supplies the connection
settings DeltaLLM needs. Keep any subchart passwords in existing Kubernetes Secrets rather than a
values file.

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

### 3. Split batch workers from API/UI pods

For production batch workloads, keep the API/UI/gateway Deployment latency-focused and run batch execution in a dedicated worker Deployment. The chart uses the same image for both roles, but renders separate ConfigMaps so each role can run different `general_settings`.

```yaml
config:
  general_settings:
    embeddings_batch_enabled: true
    embeddings_batch_storage_backend: s3
    embeddings_batch_s3_bucket: deltallm-batch-artifacts
    embeddings_batch_s3_region: us-east-1

batchWorker:
  enabled: true
  replicaCount: 2
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: 2000m
      memory: 2Gi
  config:
    general_settings:
      embeddings_batch_worker_concurrency: 2
      embeddings_batch_item_claim_limit: 10
```

When `batchWorker.enabled=true`, the chart automatically disables batch executor, completion outbox, and cleanup loops in the API ConfigMap and enables them in the worker ConfigMap. `config` remains the shared base config, `api.config` overrides only API/UI/gateway pods, and `batchWorker.config` overrides only worker pods.

The Service keeps the legacy API selector for upgrade safety. Worker pods use a distinct `app.kubernetes.io/name`, so they are not routed by the public Service. When `prometheus.serviceMonitor.enabled=true`, split mode also renders a worker-only metrics Service and ServiceMonitor so API and worker metrics can be scraped separately.

Shared mode is acceptable for evaluation and small single-replica deployments. For platform workloads that serve UI navigation, synchronous gateway traffic, and batches at the same time, split mode gives each workload its own scaling envelope:

- shared mode upper bound: `api replicas * embeddings_batch_worker_concurrency`
- split mode upper bound: `batchWorker replicas * embeddings_batch_worker_concurrency`

Use S3 batch artifact storage for split mode. The chart rejects enabled batching with local artifact storage in split mode because API pods and worker pods cannot safely share local files. For single-node development only, set `batchWorker.allowUnsafeLocalStorage=true` to bypass the guard.

### 4. Provider credentials and platform settings

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

### 5. Model deployment lifecycle

The production profile stores model deployments in the database and does not seed them from a
configuration file:

```yaml
config:
  general_settings:
    cache_backend: redis
    model_deployment_source: db_only
    model_deployment_bootstrap_from_config: false
```

Create the first model through the Admin UI or Admin API after installation. If you need to import
models from a configuration file, see [Model deployments](../configuration/models.md) for the
bootstrap modes and their safety rules.

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
helm upgrade --install deltallm deltallm/deltallm \
  --version <chart-version> \
  --namespace deltallm \
  --create-namespace \
  -f values-production.yaml \
  -f values-custom.yaml
```

`values-production.yaml` gives you:

- `replicaCount: 3`
- HPA enabled
- PDB enabled
- topology spread constraints
- soft anti-affinity
- bundled PostgreSQL and Redis disabled
- `cache_backend: redis` (shared cache across replicas)
- `model_deployment_source: db_only` (database-managed models)
- `model_deployment_bootstrap_from_config: false` (no auto-seeding)

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

## Production migration sequence

The current container image still bootstraps Prisma on startup by default.

The chart exposes an optional `migrationJob`, but its built-in hook is a
`post-install,post-upgrade` hook. It does not prove that schema migration finishes before a
Deployment rollout. Its default command is also the release-specific organization-deletion
coordinator rather than a generic migration-only contract.

For production, make migration an explicit delivery stage before `helm upgrade`:

1. Pin the application image by release tag and, preferably, digest.
2. Run one retry-safe migration job using that exact release image.
3. Wait for the job and release-specific verification to pass.
4. Roll API and worker replicas with an explicit application command that bypasses the image's
   bootstrap wrapper.

Use this values override after the separate migration succeeds:

```yaml
migrationJob:
  enabled: false

command: ["uvicorn"]
args: ["src.main:app", "--host", "0.0.0.0", "--port", "4000"]
```

The global command also applies to the chart's batch-worker Deployment; each role still receives
its role-specific configuration. If a release calls for the organization-deletion coordinator or
another special cutover, run that documented workflow instead of the generic command and keep its
feature gate disabled until every required verification passes.

See [Database migrations](database-migrations.md) for the job manifest and ordering contract and
[Upgrades and rollbacks](upgrade-and-rollback.md) for the complete release procedure.

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

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pod exits during startup | Missing `master-key` or `salt-key` | Set `secret.values.*` or `secret.existingSecret` |
| App cannot connect to PostgreSQL | Wrong external DB secret or bundled PostgreSQL disabled | Check `runtime.database.*` and subchart settings |
| App cannot connect to Redis | Wrong Redis URL or missing Redis auth password | Check `runtime.redis.*` or bundled `redis.auth.password` |
| Provider calls fail immediately | Missing provider env vars | Add them via `envFrom` / `env` |
| Config change did not roll pods | External secret changed outside Helm | Restart the deployment or rotate through your secret operator |
| Release migration job fails | DB not reachable, migration history conflict, or wrong release workflow | Stop rollout, preserve job logs, and follow [Database migrations](database-migrations.md); do not use `db push` |
