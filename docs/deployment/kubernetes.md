# Kubernetes Deployment

Deploy DeltaLLM on Kubernetes with the Helm chart in [`helm/`](/Users/mehditantaoui/Documents/Challenges/deltallm/helm).

The rewritten chart supports three concrete deployment shapes:

- evaluation with bundled PostgreSQL and Redis
- standard production with external PostgreSQL and Redis
- high-availability production with multiple replicas, HPA, PDB, topology spread, ingress, and monitoring

## Prerequisites

- Kubernetes 1.24+
- Helm 3.10+
- `kubectl` access to the target cluster

## Fetch chart dependencies

The chart uses Bitnami PostgreSQL and Redis as optional subcharts.

```bash
helm dependency build ./helm
```

## Chart profiles

The chart now ships with three value layers:

- [`helm/values.yaml`](/Users/mehditantaoui/Documents/Challenges/deltallm/helm/values.yaml): safe baseline
- [`helm/values-eval.yaml`](/Users/mehditantaoui/Documents/Challenges/deltallm/helm/values-eval.yaml): quick-start with bundled PostgreSQL and Redis
- [`helm/values-production.yaml`](/Users/mehditantaoui/Documents/Challenges/deltallm/helm/values-production.yaml): HA-oriented production defaults

## Quick start

This path uses bundled PostgreSQL and Redis and generated control-plane secrets.

```bash
helm upgrade --install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  -f helm/values-eval.yaml \
  --set secret.values.masterKey="StrongMasterKey2026SecureValue99" \
  --set secret.values.saltKey="unique-salt-2026"
```

Access the service with port-forwarding:

```bash
kubectl port-forward -n deltallm svc/deltallm 4000:4000
curl http://localhost:4000/health/liveliness
```

Open the admin UI at `http://localhost:4000`.

## Secret layout

For production, keep secrets out of Helm values.

Create one secret for `master-key` and `salt-key`:

```bash
kubectl create secret generic deltallm-app-secrets \
  --namespace deltallm \
  --from-literal=master-key='StrongMasterKey2026SecureValue99' \
  --from-literal=salt-key='unique-salt-2026'
```

Create one secret for runtime environment variables:

```bash
kubectl create secret generic deltallm-runtime-secrets \
  --namespace deltallm \
  --from-literal=DATABASE_URL='postgresql://user:pass@postgres:5432/deltallm' \
  --from-literal=DELTALLM_DATABASE_URL='postgresql+asyncpg://user:pass@postgres:5432/deltallm' \
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
      asyncUrlKey: DELTALLM_DATABASE_URL
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
      asyncUrlKey: DELTALLM_DATABASE_URL
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
      asyncUrlKey: DELTALLM_DATABASE_URL
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
