# Realtime audio over WebSockets

DeltaLLM provides the native OpenAI `WS /v1/realtime` endpoint for generated speech,
conversational audio, and live transcription. Enable it explicitly after applying
migrations. Use a stored DeltaLLM API key in the `Authorization: Bearer ...` header.
Provider credentials stay on the gateway.

This first runtime supports **manual turns** on the public OpenAI endpoint.
It is opt-in and still requires qualification against your exact upstream model
before production rollout. The existing HTTP `/v1/audio/speech` endpoint remains
the API for reading supplied text verbatim.

## Supported scope

| Capability | Behavior |
| --- | --- |
| Conversation and generated speech | `?model=your-alias`, native text/audio events and client function tools |
| Live transcription | `?intent=transcription&model=your-alias`, or the configured default transcription alias |
| Turn control | Manual audio commits and `response.create`; one active billable turn per session |
| Interruption | Client sends `response.cancel` and truncates unplayed audio using native events |
| Accounting | Exact text, audio, cache, or transcription-duration receipts through the existing spend outbox |
| Limits | Shared connection leases; key/user/team/org/model/tier request quotas per billable turn; bounded session duration, input, and events |
| Credentials and permissions | Existing model grants and tier policies; route and prices pinned at admission; periodic revocation checks |

The gateway rejects these profiles and inputs:

- Automatic VAD.
- Scopes with budget caps.
- Token or audio quota profiles.
- Legacy key concurrency caps.
- Configured guardrails.
- Prompt references.
- Hosted tools.
- Image input.
- Browser authentication.
- Alternative provider origins.

The gateway rejects unsupported profiles. It does not silently bypass their controls.
Shared Realtime and tier connection caps still apply to supported keys.
Do not remove an organization's limits to permit a session connection.

## Configure

Realtime requires Redis, a migrated database, and the enabled durable spend worker:

```yaml
general_settings:
  spend_ingestion_mode: outbox
  spend_ingestion_worker_enabled: true
  realtime:
    enabled: true
    default_transcription_model: live-transcription
    max_connections: 64
    global_max_connections: 256
    organization_max_connections: 64
    max_turns: 100
    max_output_tokens: 4096
    session_seconds: 300
    idle_seconds: 60
    health_seconds: 5
```

`realtime` is a startup-only object, also available as `DELTALLM_REALTIME` JSON.
An explicitly configured YAML object takes precedence over the environment object.
The remaining bounds are `max_message_bytes` (1 MiB), `max_input_bytes` (64 MiB),
`max_client_events` (10,000), `max_server_events` (100,000), `handshake_seconds` (10),
`write_seconds` (10), and `cleanup_seconds` (5). Session duration cannot exceed
3,600 seconds. Change these settings with a restart.

Add an OpenAI deployment with `model_info.mode: realtime`. In the admin model form,
choose **Realtime Audio**, then the session and usage types. For example:

```yaml
model_list:
  - model_name: voice
    deltallm_params:
      model: openai/gpt-realtime
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: realtime
      realtime_profile: realtime
      realtime_usage_type: tokens
      # Illustrative rates only; replace every rate with your exact model's prices.
      input_cost_per_token: 0.000001
      output_cost_per_token: 0.000002
      input_cost_per_audio_token: 0.000003
      output_cost_per_audio_token: 0.000004
      input_cost_per_token_cache_hit: 0.0000005
      input_cost_per_audio_token_cache_hit: "0.0000003"
  - model_name: live-transcription
    deltallm_params:
      model: openai/gpt-live-transcribe
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: realtime
      realtime_profile: transcription
      realtime_usage_type: duration
      # Illustrative USD/second rate; replace using the provider rate card.
      input_cost_per_second: 0.0001
```

For a token-metered transcription model, use `realtime_usage_type: tokens` and
configure input text, input audio, and output text prices. Missing cache discounts
use the corresponding full input price. Missing required prices deny admission.

## Client flow

Connect to `wss://your-gateway/v1/realtime?model=voice`. The gateway configures
manual turns upstream before accepting the client connection. The first native
`session.created` and `session.updated` events describe this setup. Keep
`audio.input.turn_detection` set to `null` in subsequent session updates.

For generated speech from a text instruction:

```json
{"type":"conversation.item.create","item":{"type":"message","role":"user","content":[{"type":"input_text","text":"Give a brief spoken greeting."}]}}
{"type":"response.create","response":{"output_modalities":["audio"]}}
```

Play `response.output_audio.delta` chunks and wait for `response.done` before
starting another response. For conversational audio, append base64 audio with
`input_audio_buffer.append`, commit it with `input_audio_buffer.commit`, then send
`response.create`. Use the format and sample rate configured in the session.

For transcription, connect with `?intent=transcription&model=live-transcription`.
Append audio, commit the turn, and wait for
`conversation.item.input_audio_transcription.completed` before appending the next
turn. The first append records durable intent because streaming transcription
may start work before commit. A buffer clear after transcription has started is
rejected. Transcript deltas pass through unchanged.

The [OpenAI WebSocket guide](https://developers.openai.com/api/docs/guides/voice-websockets)
and [transcription guide](https://developers.openai.com/api/docs/guides/realtime-transcription)
describe the native events and SDK connections. Use your gateway base URL and
DeltaLLM key with the SDK; do not send an `OpenAI-Beta` header.

The repository tests OpenAI Python SDK `3.22.1` against the bootstrapped gateway,
with a local provider peer. Disable client reconnection so failed billable turns
are not silently retried:

```python
import asyncio
import os
from openai import AsyncOpenAI

async def main():
    async with AsyncOpenAI(
        base_url="https://your-gateway/v1",
        api_key=os.environ["DELTALLM_API_KEY"],
    ) as client:
        async with client.realtime.connect(model="voice", max_retries=0) as session:
            await session.conversation.item.create(item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Give a brief spoken greeting."}],
            })
            await session.response.create(response={"output_modalities": ["audio"]})
            async for event in session:
                if event.type == "response.output_audio.delta":
                    pass  # Decode event.delta and play using the session's audio format.
                elif event.type in {"response.done", "error"}:
                    break

asyncio.run(main())
```

For transcription, pass `model="live-transcription"` and
`extra_query={"intent": "transcription"}` to `connect`, then use
`session.input_audio_buffer.append` and `session.input_audio_buffer.commit`.

## Operations and recovery

A terminal result is forwarded only after its usage receipt is durable. The
existing spend worker applies it once to the existing ledger and scope totals.
Repeated identical receipts have one economic effect. Conflicting or unknown
usage stops the session. A disconnect, process failure, or missing receipt leaves
a pending accounting record; recovery never retries provider work or assumes a
zero charge. Accepted receipts continue recovering when new sessions are disabled.

Each turn's reporting time starts when its dispatch intent is recorded and ends
when its first receipt is accepted. Idle time before the turn is excluded. Session
start time is retained separately in metadata. Duplicate receipts and recovery
preserve the first accepted timestamps; previously accepted records are unchanged.
This duration measures dispatch through receipt acceptance, including transcription
input time, rather than time to the first audio chunk.

A successfully completed turn can restore a deployment recovering from cooldown.
Cancelled, failed and incomplete responses release their capacity without marking
the deployment healthy. A newer manual cooldown remains authoritative.
If Redis cannot confirm the release, the session closes and finalization retries
within the existing cleanup deadline. Durable usage is retained. If cleanup also
fails, Realtime readiness fails and the shared permit expires by its lease deadline.

The journal retains frozen attribution, rate cards, normalized usage and state;
it does not retain audio, transcripts, instructions, or provider credentials.
Unresolved records count against the bounded journal capacity. Settled journal
records expire after 30 days; pending records need reconciliation from authoritative
provider usage before they can be resolved. No automatic refund or estimate is made.

Monitor `deltallm_realtime_active_sessions`, `deltallm_realtime_sessions_total`
and `deltallm_realtime_receipts_total`. Investigate pending receipts and
`realtime_recovery` spend-ingestion failures before the journal reaches its
100,000 unresolved-record limit. Alert thresholds should match your expected
connection count and accounting delay.

Readiness includes the Realtime runtime when enabled. Shutdown stops new sessions,
closes active sockets, and finalizes intents before stopping the spend worker.
Set ingress WebSocket read/write timeouts above your session duration and allow
Upgrade headers. On Kubernetes, `terminationGracePeriodSeconds` must exceed
`cleanup_seconds + write_seconds + telemetry_shutdown_drain_timeout_seconds`;
with defaults, use at least 45 seconds. The Helm chart validates this bound.

When upgrading to these shared-lease fixes, disable new Realtime admissions and
drain all sockets on the old version before enabling the upgraded runtime. An old
WebSocket worker can still shorten a concurrency key shared with HTTP requests.
Keep the spend worker running and retain pending accounting records throughout
the upgrade. Rolling back should also disable new admissions and drain sessions;
accepted receipts can continue settling with Realtime disabled.

Before rollout, do these qualification steps:

1. Verify a conversation with the exact model.
2. Verify text-to-audio output.
3. Verify transcription.
4. Verify cancellation and disconnect behavior.
5. Reconcile provider usage for these operations.
6. Do tests with Redis and database failures.
7. Do tests with multiple replicas.
8. Measure latency and memory at the intended connection count.

Live OpenAI qualification and capacity measurements are release checks.
They are separate from the repository's deterministic tests and local-service tests.
