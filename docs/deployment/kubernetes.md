# Kubernetes Deployment

Deploy DeltaLLM on Kubernetes using the included Helm chart.

## Prerequisites

- Kubernetes cluster (1.24+)
- Helm 3
- PostgreSQL database (managed or in-cluster)
- Redis (managed or in-cluster)

## Install with Helm

```bash
helm install deltallm ./helm/deltallm \
  --namespace deltallm \
  --create-namespace \
  --set config.masterKey=sk-your-master-key \
  --set config.saltKey=your-salt-key \
  --set database.url=postgresql://user:pass@host:5432/deltallm \
  --set redis.host=redis-host
```

## Values

Key Helm values:

| Value | Default | Description |
|-------|---------|-------------|
| `replicaCount` | `1` | Number of DeltaLLM pods |
| `config.masterKey` | — | Master API key |
| `config.saltKey` | — | Salt for key hashing |
| `database.url` | — | PostgreSQL connection string |
| `redis.host` | `localhost` | Redis hostname |
| `redis.port` | `6379` | Redis port |
| `service.port` | `5000` | Service port |
| `ingress.enabled` | `false` | Enable Ingress |
| `resources.requests.cpu` | `250m` | CPU request |
| `resources.requests.memory` | `512Mi` | Memory request |

## Scaling

DeltaLLM is stateless (state is in PostgreSQL and Redis), so horizontal scaling is straightforward:

```bash
kubectl scale deployment deltallm --replicas=3 -n deltallm
```

When running multiple replicas:

- Use Redis (not memory) for caching to share cache across instances
- Rate limiting is coordinated through Redis automatically
- Each instance connects to the same PostgreSQL database

## Health Checks

The Helm chart configures Kubernetes probes automatically:

```yaml
livenessProbe:
  httpGet:
    path: /health/liveliness
    port: 5000
  initialDelaySeconds: 10
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /health/readiness
    port: 5000
  initialDelaySeconds: 5
  periodSeconds: 10
```

## ConfigMap for config.yaml

Mount your configuration as a ConfigMap:

```bash
kubectl create configmap deltallm-config \
  --from-file=config.yaml=./config.yaml \
  -n deltallm
```

Reference secrets using environment variables rather than embedding them in the ConfigMap.
