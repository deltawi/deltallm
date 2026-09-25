# Caching

DeltaLLM can cache repeat requests to reduce latency and provider cost.

## Try caching locally

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

Restart DeltaLLM, then set the address and application key for your environment:

```bash
export BASE_URL="http://localhost:4002"
export API_KEY="YOUR_APPLICATION_KEY"
```

Use `http://localhost:8000` for the manual development setup.

Run this command twice:

```bash
curl -i "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Reply with one word: hello"}]
  }'
```

The first response should include `x-deltallm-cache-hit: false`. The second should include
`x-deltallm-cache-hit: true`.

## What gets cached

The cache middleware is currently applied to these POST endpoints:

- `/v1/chat/completions`
- `/v1/completions`
- `/v1/responses`
- `/v1/embeddings`

Streaming cache replay is currently supported only for `/v1/chat/completions`.

## Choose a backend

| Backend | Best for | Notes |
|---------|----------|-------|
| `memory` | Local development or a single instance | No external dependency |
| `redis` | Shared cache across multiple instances | Best production default |

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

## Verify cache hits

Cached responses include:

| Header | Meaning |
|--------|---------|
| `x-deltallm-cache-hit` | `true` when the response came from cache |
| `x-deltallm-cache-key` | The cache key used for this response |

## Control caching for one request

### Use request metadata

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

### Use HTTP headers

The cache middleware also reads:

- `Cache-Control: no-cache`
- `Cache-Control: no-store`
- `Cache-TTL: <seconds>`

## Learn more

- [Observability](observability.md)
- [Caching details](../reference/caching.md)
- [Configuration reference](../configuration/general.md)
