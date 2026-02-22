# Caching

DeltaLLM can cache LLM responses to reduce latency and costs for repeated or similar requests.

## Enabling Cache

```yaml
general_settings:
  cache_enabled: true
  cache_backend: memory
  cache_ttl: 3600
  cache_max_size: 10000
```

## Cache Backends

### Memory Cache

In-process cache with LRU eviction. No external dependencies required.

```yaml
general_settings:
  cache_backend: memory
  cache_max_size: 10000
```

Best for single-instance deployments or development.

### Redis Cache

Distributed cache shared across multiple DeltaLLM instances.

```yaml
general_settings:
  cache_backend: redis
  redis_host: localhost
  redis_port: 6379
```

Best for production multi-instance deployments.

### S3 Cache

Long-term cache storage using S3-compatible object storage.

```yaml
general_settings:
  cache_backend: s3
```

Configure S3 settings in `deltallm_settings.callback_settings.s3`.

## Cache Key Composition

Cache keys are generated from:

- The model name
- The complete message payload
- Relevant request parameters (temperature, max_tokens, etc.)

Two identical requests produce the same cache key and return the cached response.

## Cache Headers

Cached responses include headers to indicate a cache hit:

| Header | Description |
|--------|-------------|
| `x-deltallm-cache-hit` | `true` if the response was served from cache |
| `x-deltallm-cache-key` | The cache key used for this request |

## Per-Request Cache Control

Clients can control caching on a per-request basis using metadata:

```json
{
  "model": "gpt-4o-mini",
  "messages": [{"role": "user", "content": "Hello"}],
  "metadata": {
    "cache": {
      "no-cache": true
    }
  }
}
```
