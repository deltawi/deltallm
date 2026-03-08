# Caching

DeltaLLM can cache repeat requests to reduce latency and provider cost.

## Quick Success Path

1. Turn caching on
2. Start with the in-memory backend
3. Send the same request twice
4. Check `x-deltallm-cache-hit` in the response headers

```yaml
general_settings:
  cache_enabled: true
  cache_backend: memory
  cache_ttl: 3600
  cache_max_size: 10000
```

## What Gets Cached

The cache middleware is currently applied to these POST endpoints:

- `/v1/chat/completions`
- `/v1/completions`
- `/v1/responses`
- `/v1/embeddings`

Streaming cache replay is currently supported only for `/v1/chat/completions`.

## Choose a Backend

| Backend | Best for | Notes |
|---------|----------|-------|
| `memory` | Local development or a single instance | No external dependency |
| `redis` | Shared cache across multiple instances | Best production default |
| `s3` | Long-lived object-backed cache | Use when you need object storage instead of RAM or Redis |

### Memory

```yaml
general_settings:
  cache_backend: memory
  cache_max_size: 10000
```

### Redis

```yaml
general_settings:
  cache_backend: redis
  redis_url: os.environ/REDIS_URL
```

### S3

```yaml
general_settings:
  cache_backend: s3
```

When you use `s3`, also configure the S3 callback settings under `deltallm_settings.callback_settings.s3`.

## How Cache Keys Work

DeltaLLM builds cache keys from:

- the full request payload
- the target model
- relevant request parameters
- an optional custom cache key
- the authenticated request scope

This means two different API keys do not share cache entries by default.

## Verify Cache Hits

Cached responses include:

| Header | Meaning |
|--------|---------|
| `x-deltallm-cache-hit` | `true` when the response came from cache |
| `x-deltallm-cache-key` | The cache key used for this response |

## Control Caching Per Request

### Disable or Relax Caching Through Metadata

```json
{
  "model": "gpt-4o-mini",
  "messages": [{"role": "user", "content": "Hello"}],
  "metadata": {
    "cache": "no-cache",
    "cache_ttl": 120,
    "cache_key": "my-shared-key"
  }
}
```

Supported request-level controls:

- `metadata.cache: false` skips cache lookup and cache write
- `metadata.cache: "no-cache"` skips lookup but allows a fresh write
- `metadata.cache: "no-store"` allows lookup but skips write
- `metadata.cache_ttl` overrides TTL
- `metadata.cache_key` provides a custom logical key

### Use HTTP Headers

The cache middleware also reads:

- `Cache-Control: no-cache`
- `Cache-Control: no-store`
- `Cache-TTL: <seconds>`

## Advanced Notes

- Cache accounting still updates request, usage, and spend metrics on cache hits
- Budget enforcement and auth still happen before a cached response is returned
- If caching is disabled globally, request-level cache metadata has no effect

## Related Pages

- [Observability](observability.md)
- [Configuration Reference](../configuration/general.md)
