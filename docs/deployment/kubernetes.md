# Kubernetes Deployment

Deploy DeltaLLM on Kubernetes using the included Helm chart. The chart packages everything you need: the DeltaLLM proxy server, optional in-cluster PostgreSQL and Redis, health probes, autoscaling, Prometheus metrics, and S3 request logging.

## Prerequisites

- Kubernetes cluster (1.24+)
- Helm 3.10+
- `kubectl` configured for your cluster

## Quick Start

### 1. Add Dependencies

The chart includes optional Bitnami subcharts for PostgreSQL and Redis. Pull them first:

```bash
cd helm
helm dependency update
```

### 2. Install

```bash
helm install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  --set config.masterKey="sk-your-master-key-at-least-32-chars-long1" \
  --set config.saltKey="your-unique-random-salt-string"
```

This deploys DeltaLLM with in-cluster PostgreSQL and Redis using default credentials. The gateway is available on port 4000 inside the cluster.

!!! warning "Change default credentials"
    The default PostgreSQL password is `deltallm`. Always override it for non-development deployments:

    ```bash
    --set postgresql.auth.password="strong-random-password"
    ```

### 3. Verify

```bash
kubectl get pods -n deltallm
kubectl logs -l app.kubernetes.io/name=deltallm -n deltallm
```

Test the health endpoint:

```bash
kubectl port-forward svc/deltallm 4000:4000 -n deltallm
curl http://localhost:4000/health/liveliness
```

### 4. Access the Admin UI

The admin dashboard is served at the root path. After port-forwarding:

1. Open `http://localhost:4000` in your browser
2. Enter your master key to log in
3. Add model deployments through the Models page

---

## Configuration Reference

All configuration is managed through Helm values. Create a `values-custom.yaml` file for your deployment:

```bash
helm install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  -f values-custom.yaml
```

### Required Values

These must be set for every deployment:

| Value | Description |
|-------|-------------|
| `config.masterKey` | Admin master key (min 32 characters, must contain letters and digits) |
| `config.saltKey` | Salt for API key hashing (must be unique per deployment) |

When using external databases (with `postgresql.enabled: false`), you must also set:

| Value | Description |
|-------|-------------|
| `config.databaseUrl` | PostgreSQL connection string (`postgresql://user:pass@host:5432/db`) |
| `config.redisUrl` | Redis connection string (required for multi-replica coordination) |

### Core Settings

| Value | Default | Description |
|-------|---------|-------------|
| `replicaCount` | `2` | Number of DeltaLLM pods (ignored when HPA is enabled) |
| `image.repository` | `deltallm/deltallm` | Container image |
| `image.tag` | `latest` | Image tag |
| `image.pullPolicy` | `IfNotPresent` | Container image pull policy |
| `service.type` | `ClusterIP` | Kubernetes Service type |
| `service.port` | `4000` | Port the gateway listens on |
| `serviceAccount.create` | `true` | Create a dedicated ServiceAccount |
| `serviceAccount.annotations` | `{}` | Annotations for the ServiceAccount (useful for IRSA) |
| `nodeSelector` | `{}` | Node selector constraints |
| `tolerations` | `[]` | Pod tolerations |
| `affinity` | `{}` | Pod affinity/anti-affinity rules |

### Database

DeltaLLM requires PostgreSQL. You can use the bundled Bitnami subchart or provide an external database.

=== "In-Cluster PostgreSQL (default)"

    ```yaml
    postgresql:
      enabled: true
      auth:
        username: deltallm
        password: strong-random-password
        database: deltallm
      primary:
        persistence:
          enabled: true
          size: 10Gi
    ```

=== "External PostgreSQL"

    ```yaml
    postgresql:
      enabled: false

    config:
      databaseUrl: "postgresql://user:pass@your-rds-host:5432/deltallm"
      asyncDatabaseUrl: "postgresql+asyncpg://user:pass@your-rds-host:5432/deltallm"
    ```

!!! note "Dual database URLs"
    DeltaLLM uses two database connections internally:

    - `DATABASE_URL` — Used by Prisma ORM (`postgresql://` prefix)
    - `DELTALLM_DATABASE_URL` — Used by SQLAlchemy async engine (`postgresql+asyncpg://` prefix)

    When using the in-cluster PostgreSQL subchart, both are generated automatically with the correct prefixes. When providing an external URL via `config.databaseUrl`, the chart auto-converts the prefix for the async connection. You can also set `config.asyncDatabaseUrl` explicitly if your async connection differs.

### Redis

Redis is used for distributed caching, rate limiting coordination, and config propagation across replicas. It is optional but recommended for multi-replica deployments.

=== "In-Cluster Redis (default)"

    ```yaml
    redis:
      enabled: true
      auth:
        enabled: false
      master:
        persistence:
          enabled: true
          size: 5Gi
    ```

=== "External Redis"

    ```yaml
    redis:
      enabled: false

    config:
      redisUrl: "redis://your-elasticache-host:6379/0"
    ```

=== "No Redis"

    ```yaml
    redis:
      enabled: false
    ```

    Without Redis, DeltaLLM falls back to in-memory caching and per-instance rate limiting. Settings changes will not propagate across replicas.

### Model Deployments

Models are configured in the `config.model_list` array. Each entry maps a virtual model name to a provider backend:

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
        input_cost_per_token: 0.0000025
        output_cost_per_token: 0.00001

    - model_name: claude-3-5-sonnet
      deltallm_params:
        model: anthropic/claude-3-5-sonnet-20241022
        api_key: os.environ/ANTHROPIC_API_KEY
        timeout: 300
      model_info:
        mode: chat
        input_cost_per_token: 0.000003
        output_cost_per_token: 0.000015
```

!!! tip "Runtime model management"
    With `model_deployment_source: hybrid` (the default), you can add and remove models through the Admin UI or API without redeploying the Helm chart. Models defined in the config are bootstrapped on startup, and runtime changes are persisted to the database.

### Router Settings

Control how requests are distributed across model deployments:

```yaml
config:
  router_settings:
    routing_strategy: simple-shuffle   # round-robin, weighted, least-busy, latency-based, cost-based
    num_retries: 3                     # Retry count on provider failure
    retry_after: 1                     # Seconds between retries
    timeout: 300                       # Request timeout in seconds
    cooldown_time: 60                  # Seconds to exclude a failing deployment
    allowed_fails: 0                   # Failures before cooldown triggers
```

### General Settings

```yaml
config:
  general_settings:
    cache_enabled: false
    cache_backend: memory              # memory, redis
    cache_ttl: 3600
    background_health_checks: true
    health_check_interval: 300
    model_deployment_source: hybrid    # hybrid, db_only, config_only
```

---

## Ingress

The chart includes an Ingress resource, enabled by default:

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
  hosts:
    - host: deltallm.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: deltallm-tls
      hosts:
        - deltallm.example.com
```

!!! tip "Long timeouts"
    LLM requests can take 30+ seconds. Set proxy timeouts to at least 600 seconds to avoid premature disconnects during streaming responses.

---

## Autoscaling

Horizontal Pod Autoscaling is enabled by default:

```yaml
hpa:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```

DeltaLLM is stateless — all persistent state lives in PostgreSQL and Redis. This makes horizontal scaling straightforward. When running multiple replicas:

- Settings changes propagate across instances via Redis pub/sub
- Rate limiting is coordinated through Redis
- Cache is shared when using Redis as the cache backend

---

## Health Checks

The chart configures Kubernetes liveness and readiness probes automatically:

| Probe | Endpoint | Purpose |
|-------|----------|---------|
| Liveness | `/health/liveliness` | Restarts the pod if the process is hung |
| Readiness | `/health/readiness` | Removes the pod from the Service if PostgreSQL or Redis is unreachable |

Default probe timing:

```yaml
probes:
  liveness:
    path: /health/liveliness
    initialDelaySeconds: 30
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    path: /health/readiness
    initialDelaySeconds: 5
    periodSeconds: 5
    timeoutSeconds: 3
    failureThreshold: 3
```

---

## Prometheus Metrics

DeltaLLM exposes Prometheus metrics at `/metrics`. The chart can automatically configure scraping.

### Pod Annotations

When `prometheus.enabled` is `true` (the default), standard Prometheus scrape annotations are added to the pod template:

```yaml
prometheus:
  enabled: true
```

This adds:

```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "4000"
  prometheus.io/path: "/metrics"
```

The `prometheus` callback is also automatically injected into the DeltaLLM callback list, activating metric collection for requests, tokens, spend, latency, and cache performance.

### ServiceMonitor (Prometheus Operator)

If you use the Prometheus Operator, enable the ServiceMonitor CRD:

```yaml
prometheus:
  enabled: true
  serviceMonitor:
    enabled: true
    interval: 30s
    scrapeTimeout: 10s
    labels:
      release: prometheus    # Match your Prometheus Operator's selector
```

The ServiceMonitor is created in the same namespace as the release by default. To place it in a different namespace:

```yaml
prometheus:
  serviceMonitor:
    namespace: monitoring
```

### Available Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `deltallm_requests_total` | Counter | model, api_provider, user, team, status_code |
| `deltallm_request_failures_total` | Counter | model, api_provider, error_type |
| `deltallm_input_tokens_total` | Counter | model, api_provider |
| `deltallm_output_tokens_total` | Counter | model, api_provider |
| `deltallm_spend_total` | Counter | model, api_provider |
| `deltallm_request_total_latency_seconds` | Histogram | model, api_provider |
| `deltallm_llm_api_latency_seconds` | Histogram | model, api_provider |
| `deltallm_deployment_state` | Gauge | deployment_id |
| `deltallm_deployment_active_requests` | Gauge | deployment_id |
| `deltallm_deployment_cooldown` | Gauge | deployment_id |
| `deltallm_cache_hit_total` | Counter | model |
| `deltallm_cache_miss_total` | Counter | model |

---

## S3 Request Logging

DeltaLLM can log request and response payloads to an S3 bucket for auditing and analytics. Logs are partitioned by date (`year=YYYY/month=MM/day=DD/`) for easy querying with Athena or similar tools.

### Enable S3 Logging

```yaml
s3:
  enabled: true
  bucket: "your-deltallm-logs-bucket"
  region: "us-east-1"
  prefix: "deltallm-logs/"
  compression: "gzip"        # Optional: compress logs with gzip
```

When enabled, the chart:

1. Adds `s3` to both `success_callback` and `failure_callback` lists
2. Configures the S3 callback settings in the config file
3. Sets `DELTALLM_S3_BUCKET` and `AWS_DEFAULT_REGION` environment variables

### Authentication

=== "IAM Role (recommended)"

    If your pods use IAM Roles for Service Accounts (IRSA) or a similar mechanism, no additional credentials are needed:

    ```yaml
    s3:
      enabled: true
      bucket: "your-bucket"
      region: "us-east-1"

    serviceAccount:
      create: true
      annotations:
        eks.amazonaws.com/role-arn: "arn:aws:iam::123456789012:role/deltallm-s3-role"
    ```

=== "Access Keys"

    Provide AWS credentials directly. The `secretAccessKey` is stored in a Kubernetes Secret; `accessKeyId` is set as a plain environment variable:

    ```yaml
    s3:
      enabled: true
      bucket: "your-bucket"
      region: "us-east-1"
      accessKeyId: "AKIAIOSFODNN7EXAMPLE"
      secretAccessKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    ```

    !!! tip "Use existing secrets"
        For tighter control, create a Kubernetes Secret manually and reference it via environment variable overrides in `podAnnotations` or an init container, rather than storing credentials in Helm values.

### S3 Bucket Policy

The IAM role or user needs `s3:PutObject` permission on the target bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::your-deltallm-logs-bucket/deltallm-logs/*"
    }
  ]
}
```

---

## Production Deployment

The chart includes a `values-production.yaml` overlay with production-appropriate defaults. Use it as a starting point:

```bash
helm install deltallm ./helm \
  --namespace deltallm \
  --create-namespace \
  -f helm/values-production.yaml \
  -f values-custom.yaml
```

### Recommended Production Configuration

```yaml
# values-custom.yaml
replicaCount: 3

image:
  tag: "v0.1.0"     # Pin to a specific version

config:
  masterKey: "sk-your-production-master-key-min-32-chars"
  saltKey: "your-unique-production-salt-key"
  databaseUrl: "postgresql://deltallm:password@your-rds-host:5432/deltallm"
  redisUrl: "redis://your-elasticache-host:6379/0"

  general_settings:
    background_health_checks: true
    health_check_interval: 120
    cache_enabled: true
    cache_backend: redis
    model_deployment_source: hybrid

postgresql:
  enabled: false     # Use external managed database

redis:
  enabled: false     # Use external managed Redis

prometheus:
  enabled: true
  serviceMonitor:
    enabled: true
    interval: 15s

s3:
  enabled: true
  bucket: "company-deltallm-logs"
  region: "us-east-1"
  compression: "gzip"

hpa:
  enabled: true
  minReplicas: 3
  maxReplicas: 20

resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: 2000m
    memory: 2Gi

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: llm-gateway.company.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: llm-gateway-tls
      hosts:
        - llm-gateway.company.com
```

### Production Checklist

- [ ] Master key is at least 32 characters with letters and digits
- [ ] Salt key is unique and not the default value
- [ ] Using external managed PostgreSQL with backups enabled
- [ ] Using external managed Redis (e.g., ElastiCache, Memorystore)
- [ ] Image tag pinned to a specific version (not `latest`)
- [ ] TLS configured on the Ingress
- [ ] Resource requests and limits set appropriately
- [ ] HPA configured with sensible min/max replicas
- [ ] Prometheus scraping enabled for monitoring
- [ ] S3 logging enabled for audit trail (if required)

---

## Upgrading

```bash
helm upgrade deltallm ./helm \
  --namespace deltallm \
  -f values-custom.yaml
```

Database schema migrations run automatically on pod startup via Prisma. Configuration changes stored in the database (model deployments, settings) are preserved across upgrades.

## Uninstalling

```bash
helm uninstall deltallm --namespace deltallm
```

!!! warning "Persistent data"
    If using in-cluster PostgreSQL or Redis with persistence enabled, the PersistentVolumeClaims are **not** deleted by `helm uninstall`. Delete them manually if you want to remove all data:

    ```bash
    kubectl delete pvc -l app.kubernetes.io/instance=deltallm -n deltallm
    ```

## Troubleshooting

### Pod not starting

Check the logs for startup errors:

```bash
kubectl logs -l app.kubernetes.io/name=deltallm -n deltallm --tail=50
```

Common issues:

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Salt key is required` | Missing `config.saltKey` | Set `config.saltKey` in your values |
| `master_key must be at least 32 characters` | Master key too short | Use a longer key |
| `Connection refused` on database | PostgreSQL not ready | Wait for the PostgreSQL pod to be ready, or check `config.databaseUrl` |
| Readiness probe failing | Redis or PostgreSQL unavailable | Check connectivity to backing services |

### Checking configuration

View the generated config file:

```bash
kubectl get configmap -l app.kubernetes.io/name=deltallm -n deltallm -o yaml
```

View environment variables:

```bash
kubectl exec deploy/deltallm -n deltallm -- env | grep -E "DATABASE|REDIS|DELTALLM"
```
