# Caching details

Use this page when you need to understand how DeltaLLM separates cached responses, handles streamed
responses, or accounts for cache hits. For a guided setup, start with [Cache repeated
requests](../features/caching.md).

## How cache keys work

DeltaLLM builds a cache key from:

- the full request
- the target model
- request options that can change the answer
- an optional custom cache key
- the application key or other authenticated scope
- the cache format version
- whether the response is streamed

Two different application keys therefore do not share cached responses by default. The caller
scope is the key together with its user, team, and organization, stored as a hash so cache keys,
response headers, and request logs never contain a raw credential. JWT callers without a user ID
or email skip the cache, because they have no stable identity to separate their responses.

A streamed response also cannot be returned to a request that expects one complete response, or
the reverse.

Older streamed cache entries are treated as misses when they do not contain all the information
required by the current cache format.

## Streamed responses

Streaming cache replay is supported for `/v1/chat/completions`. DeltaLLM stores each validated server
event instead of trying to rebuild the stream from its text.

This preserves reasoning fields, refusals, tool calls, provider additions, finish reasons, and event
order. Usage information is replayed only when the request includes `stream_options.include_usage`.
The final `[DONE]` event is sent once.

DeltaLLM does not cache a stream that is incomplete, cancelled, malformed, or larger than the
configured limits.

Identical requests streamed at the same time are recorded separately, so one stream can never
mix into another's cached copy. Each complete stream is stored whole, and the last one to finish
replaces the earlier copy.

## When the cache store fails

A failed or unreadable cache read is treated as a miss, and a failed write is logged and skipped,
so requests still reach the provider. The `s3` backend is not implemented: choosing it turns
response caching off instead of failing requests.

## Access, budgets, and usage

- Authentication and budget checks still run before DeltaLLM returns a cached response.
- Request, usage, and spending records are still updated for cache hits.
- Request-level cache options have no effect when caching is turned off for the whole service.

## Streaming limits

Use `stream_cache_max_bytes` and `stream_cache_max_fragments` to limit how much streamed response
data DeltaLLM keeps for one cache entry. These limits apply to every stored event, not only text.

See the [configuration reference](../configuration/general.md) for the available cache settings.
