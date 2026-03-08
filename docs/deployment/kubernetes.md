# Kubernetes Deployment

Deploy DeltaLLM on Kubernetes with the Helm chart in [`helm/`](/Users/mehditantaoui/Documents/Challenges/deltallm/helm). The chart supports the common deployment shapes:

- In-cluster PostgreSQL and Redis for evaluation or small installs
- External PostgreSQL and Redis for production
- Existing Kubernetes Secrets for the master key, salt key, provider API keys, SSO, and JWT settings
- Optional Ingress, HPA, ServiceMonitor, and S3 request logging

## Prerequisites

- Kubernetes 1.24+
- Helm 3.10+
- `kubectl` access to the target cluster

## Fetch chart dependencies

The chart uses Bitnami PostgreSQL and Redis as optional subcharts.

```bash
helm dependency build ./helm
```

## Quick start

This path uses the bundled PostgreSQL and Redis subcharts.

```bash
helm install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  --set config.masterKey="StrongMasterKey2026SecureValue99" \
  --set config.saltKey="unique-salt-2026"
```

Ingress is disabled by default. Access the service with port-forwarding:

```bash
kubectl port-forward -n deltallm svc/deltallm 4000:4000
curl http://localhost:4000/health/liveliness
```

Open the admin UI at `http://localhost:4000`.

## Recommended secret layout

For real deployments, do not keep secrets inline in Helm values.

Create one secret for the DeltaLLM control-plane secrets:

```bash
kubectl create secret generic deltallm-app-secrets \
  --namespace deltallm \
  --from-literal=master-key='StrongMasterKey2026SecureValue99' \
  --from-literal=salt-key='unique-salt-2026'
```

Create a separate runtime secret for environment-backed settings such as `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, SSO secrets, or JWT settings:

```bash
kubectl create secret generic deltallm-runtime-secrets \
  --namespace deltallm \
  --from-literal=DATABASE_URL='postgresql://user:pass@postgres:5432/deltallm' \
  --from-literal=REDIS_URL='redis://redis:6379/0' \
  --from-literal=OPENAI_API_KEY='sk-...'
```

Then reference those secrets from Helm:

```yaml
secret:
  existingSecret: deltallm-app-secrets

envFrom:
  - secretRef:
      name: deltallm-runtime-secrets
```

This is the preferred pattern when your `config.model_list` or `general_settings` use `os.environ/...` tokens.

## Configuration patterns

### 1. Bundled PostgreSQL and Redis

This is the default.

```yaml
postgresql:
  enabled: true
  auth:
    username: deltallm
    password: change-this
    database: deltallm

redis:
  enabled: true
  auth:
    enabled: false
```

If you enable Redis auth with the bundled subchart, you must also set `redis.auth.password` so the DeltaLLM deployment can build the correct connection URL:

```yaml
redis:
  enabled: true
  auth:
    enabled: true
    password: strong-redis-password
```

### 2. External PostgreSQL and Redis

Disable the bundled subcharts and inject connection strings through values or `envFrom`.

```yaml
postgresql:
  enabled: false

redis:
  enabled: false

config:
  databaseUrl: "postgresql://user:pass@postgres:5432/deltallm"
  redisUrl: "redis://redis:6379/0"
```

Using `envFrom` is usually better:

```yaml
postgresql:
  enabled: false

redis:
  enabled: false

secret:
  existingSecret: deltallm-app-secrets

envFrom:
  - secretRef:
      name: deltallm-runtime-secrets
```

The chart no longer emits empty `DATABASE_URL` or `REDIS_URL` values, so secret-based injection works cleanly.

### 3. Provider credentials and auth settings

Use `envFrom` or `env` for provider keys and identity settings:

```yaml
envFrom:
  - secretRef:
      name: deltallm-runtime-secrets

env:
  - name: PLATFORM_BOOTSTRAP_ADMIN_EMAIL
    value: admin@example.com
```

This covers:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `AZURE_OPENAI_API_KEY`
- `PLATFORM_BOOTSTRAP_ADMIN_EMAIL`
- `PLATFORM_BOOTSTRAP_ADMIN_PASSWORD`
- SSO client credentials
- JWT / JWKS settings

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

With `hybrid`, Helm can bootstrap initial models and the Admin UI/API can manage them afterward.

## Ingress and service exposure

Ingress is opt-in.

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
  hosts:
    - host: api.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: deltallm-tls
      hosts:
        - api.example.com
```

For a `LoadBalancer` service instead:

```yaml
service:
  type: LoadBalancer
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-type: nlb
```

## S3 request logging

Enable S3 logging with IRSA/workload identity or with an existing secret.

```yaml
s3:
  enabled: true
  bucket: company-deltallm-logs
  region: us-east-1
  compression: gzip
```

### IRSA / workload identity

```yaml
serviceAccount:
  create: true
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/deltallm-s3-role
```

`serviceAccount.automountServiceAccountToken` defaults to `true` so workload identity works out of the box. If you do not rely on workload identity, you can disable it.

### Existing AWS credentials secret

```yaml
s3:
  enabled: true
  bucket: company-deltallm-logs
  region: us-east-1
  existingSecret: deltallm-aws-creds
  accessKeyIdKey: aws-access-key-id
  secretAccessKeyKey: aws-secret-access-key
```

## Production baseline

Use [`helm/values-production.yaml`](/Users/mehditantaoui/Documents/Challenges/deltallm/helm/values-production.yaml) as the starting point:

```bash
helm upgrade --install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  -f helm/values-production.yaml \
  -f values-custom.yaml
```

A reasonable production overlay looks like this:

```yaml
secret:
  existingSecret: deltallm-app-secrets

envFrom:
  - secretRef:
      name: deltallm-runtime-secrets

postgresql:
  enabled: false

redis:
  enabled: false

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

hpa:
  enabled: true
  minReplicas: 3
  maxReplicas: 20

prometheus:
  enabled: true
  serviceMonitor:
    enabled: true
    interval: 15s
```

## Operational behavior

The chart now includes a few defaults that matter operationally:

- The container runs the shared database bootstrap script before starting the API, preferring `prisma migrate deploy` and falling back to `prisma db push` for legacy or unbaselined databases
- `startupProbe` protects the pod while Prisma schema setup and app startup complete
- Config and generated secret checksums are added to the pod template so Helm-triggered config changes roll pods automatically
- Ingress is disabled by default to avoid shipping a fake hostname into clusters that do not want it
- `env` and `envFrom` are passed directly into the container for provider keys and platform integrations

## Validation

Render and lint before deploying:

```bash
helm lint ./helm \
  --set config.masterKey=StrongMasterKey2026SecureValue99 \
  --set config.saltKey=unique-salt-2026

helm template deltallm ./helm \
  --set config.masterKey=StrongMasterKey2026SecureValue99 \
  --set config.saltKey=unique-salt-2026 > /tmp/deltallm-rendered.yaml
```

After deploy:

```bash
kubectl get pods -n deltallm
kubectl logs -n deltallm deploy/deltallm --tail=100
kubectl get ingress,svc -n deltallm
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pod exits during startup | Missing `master-key` or `salt-key` | Set `config.masterKey` / `config.saltKey`, or use `secret.existingSecret` |
| Pod cannot connect to PostgreSQL | Wrong `DATABASE_URL` or bundled PostgreSQL disabled unexpectedly | Check `config.databaseUrl` or `envFrom` secret contents |
| Pod cannot connect to Redis | Wrong `REDIS_URL` or Redis auth enabled without password | Set `config.redisUrl`, or set `redis.auth.password` when using bundled Redis auth |
| Provider calls fail immediately | Provider API key missing from env | Add `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or similar via `envFrom` / `env` |
| S3 logging does not work | Missing IAM permissions or AWS creds | Use IRSA/workload identity or set `s3.existingSecret` |
| Config update did not apply | Existing external secret changed without a rollout | Restart the deployment after changing externally managed secrets |
