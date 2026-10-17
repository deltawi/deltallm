# Selector session affinity

A route group with a model selector classifies each uncached request. Without
session memory, one conversation can move between lanes and deployments on each
turn. Each move loses the provider prompt cache, and each turn pays for one
selector call and its latency. Session affinity keeps a conversation on one lane
and one deployment.

## Behavior

- The gateway finds the session of a request. The first match is used:
  1. the `x-deltallm-session-id` header, then `x-session-id`;
  2. the Claude Code `x-claude-code-session-id` header;
  3. `session_id` in the Codex `x-codex-turn-metadata` header;
  4. `metadata.session_id` in the request body;
  5. a fingerprint of the OpenAI `user` field, the first system message and the
     first user message.
- An explicit ID that is blank or longer than 255 characters gives no session.
  The gateway does not use a weaker fallback in this case.
  `x-deltallm-session-affinity: off` disables affinity for one request.
- The session key is a SHA-256 digest of the API key, the source, the session ID,
  the route group and the selector policy fingerprint. The raw ID is not stored.
  A new published policy gives new keys, so old pins stop matching.
- A pin holds the lane rank and the deployment that answered.
- If the last message is not new user text (a tool result or an assistant
  prefill), the request reuses the pinned lane. It makes no selector call, so it
  has no selector charge, admission or billing operation.
- New user text runs the selector again. If the selector picks a lower lane,
  the request keeps the pinned lane (`session_floor`). A session at the top lane
  is not classified again.
- Planning tries the pinned deployment first inside its lane. All other
  candidates keep the strategy order as failover targets. Hard filters, health,
  capability and context checks run before this step, so a pin never selects a
  deployment that cannot serve the request.
- The pin is written after the answer starts. Failover to a higher lane moves the
  pin up.
- Batch items are not sessions. Each item keeps its own durable decision.

## Configuration

Affinity is part of the selector policy. An omitted `session` block means
`enabled: true` and `ttl_seconds: 3600`:

```json
"selector": {
  "kind": "llm-tier",
  "session": {"enabled": true, "ttl_seconds": 3600}
}
```

`ttl_seconds` is 60 to 86,400. Each answer refreshes the TTL. The default is
omitted from stored policies, Redis snapshots and the routing fingerprint. Thus
existing policies keep their identity, and older gateways keep reading them.
An explicit `session` block needs all API and Batch processes on this release.

## Storage and failure behavior

| Item | Value |
| --- | --- |
| Store | Redis `cache` allocation; a bounded in-process LRU (10,000 entries) when no Redis client exists |
| Key | `deltallm:<env>:v1:selector-session:<sha256>` |
| Value | `{"v":1,"r":<rank>,"d":"<deployment id>"}`, at most 512 bytes |
| Read | One `GET` per selector request, 50 ms timeout |
| Write | One `SET ... EX ttl` per answer, in a bounded owned task set (256), 250 ms timeout |
| Cardinality | One key per active session per route group |

Redis failure, a timeout or an invalid entry is a miss. The request classifies
again, as it did before this feature. A full write set drops the write. Shutdown
waits up to one second for pending writes, then cancels them. Concurrent writes
for one session keep the last value. A pin is a routing hint, not an economic or
security record. Thus a Lua compare-and-set is not necessary.

## Latency and cost budget

A selector request adds one Redis `GET`. A pinned continuation removes one
provider call (up to the selector timeout, 750 ms by default) and its admission
and billing writes. Requests to groups without a selector do no new work.

## Metrics

- `deltallm_selector_session_lookups_total{result}`: `reused`, `reclassified`,
  `miss` or `disabled`.
- `deltallm_selector_session_store_failures_total{operation}`: `lookup`,
  `invalid_entry`, `write` or `write_dropped`.
- `deltallm_selector_decisions_total{cause}` adds the `session_pinned` and
  `session_floor` causes. Pinned decisions do not add selector latency samples.

## Alternatives

- Classify every turn and keep the higher rank, as Bifrost does for tiers only.
  This keeps the selector cost on continuations, which are most requests in
  coding-agent sessions.
- Never classify again after the first turn, as LiteLLM does. A conversation that
  gets harder then stays on a lane that is too low.
- A separate classifier, such as an embedding classifier, can replace the
  selector call later. The session layer sits before the classifier and does not
  depend on it.

## Rollback

Set `session.enabled: false` on a group, or send
`x-deltallm-session-affinity: off`. A binary rollback to a release without this
feature ignores the Redis keys, which expire by TTL. Remove explicit `session`
blocks before such a rollback, because older releases reject unknown selector
fields.
