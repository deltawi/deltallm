# Batch API And Production Setup

The Batch API processes large numbers of embedding or non-streaming chat completion requests asynchronously.
It is suitable for thousands of requests when immediate results are not necessary.

1. Upload a JSONL file.
2. Create a batch.
3. When the job completes, download the results.

This page is the main batch reference. It covers the public API, worker behavior, production configuration, scheduler sizing, monitoring, and troubleshooting.

For a short first-time tutorial, use [Process work in batches](../guides/batching.md).

The commands on this page use one base address:

```bash
export BASE_URL="http://localhost:4002"
```

Use `http://localhost:8000` for the manual development setup.

## Model selectors in chat batches

Chat batches can use a Route Group's published model selector without another setup
step. Each item is classified independently after hooks, guardrails, authorization
and caller admission. The selector sees the same minimized text/features described
in [Model router configuration](../configuration/router.md); it does not receive
the whole Batch file or another item's prompt.

To keep Batch execution simple, items with a selector anywhere in their configured
fallback topology use bounded individual execution, even when a deployment requests
`sync_microbatch`. Unrelated selector-free groups retain normal microbatching.
There is no regrouping queue or shared decision. A selector in a fallback group is
called only if execution actually reaches that group.

Customers pay the selector's actual regular provider cost, without a selector
markup or assumed Batch discount. Answers retain normal Batch pricing. Public
output `usage` remains answer-only; protected routing-cost reports link selector
and answer spend. A missing/unknown receipt is pending, not free. Budgets remain
soft admission checks, so simultaneous requests can overshoot.

The item stores its decision before the answer starts and reuses it on answer
retry or worker reclaim. Changes to normalized input, ownership or policy semantics
cannot silently reinterpret an old decision. If a worker disappears between
starting selection and saving its decision, the item returns
`batch_selector_checkpoint_unavailable` and follows existing bounded retries.
It never pays for another classifier attempt; uncertain incurred costs remain with
the existing accounting recovery owner. The rare affected item may ultimately fail.
The checkpoint starts only after successful billing admission. A rejected or
rolled-back admission can retry without a pending checkpoint or classifier cost.
Error files and admin item details preserve the stable checkpoint error code;
lists show its safe message instead of a generic provider-outage message.

Selected and ordinary answers share the chosen deployment's existing
`chat_batching.max_in_flight` cap. No selector-specific concurrency setting is
needed. Fallback releases the previous deployment's slot before taking another.

Apply migrations and drain old workers before admitting selector-enabled batches.
Constraint installation and historical validation run as separate migrations,
so the validation scan does not retain the strong installation lock.
For binary rollback, drain selected items and their completion outboxes first;
keep the additive checkpoint column. Removing the selector affects fresh items,
but incompatible checkpointed items fail safely. Batch Responses, streaming, MCP
tools and selectors for non-chat workloads remain unsupported.

The throughput tradeoff is deliberate: selecting cheaper answers may save money,
but selected items give up upstream microbatch packing and add one classifier call.
Measure with your workload and reserve realtime provider headroom using existing
Batch capacity settings. The internal design and verification record
documents ownership, recovery, dependency budgets and reproducible local measurements.

### Selector Batch example

After publishing a selector on the chat Route Group `text-router`, use the same
Files and Batch endpoints as any other chat batch. There is no separate Batch
selector switch. Save these two lines as `selector-input.jsonl`:

```jsonl
{"custom_id":"routine","method":"POST","url":"/v1/chat/completions","body":{"model":"text-router","messages":[{"role":"user","content":"Extract the city: the meeting is in Paris."}],"max_tokens":100}}
{"custom_id":"complex","method":"POST","url":"/v1/chat/completions","body":{"model":"text-router","messages":[{"role":"user","content":"Compare two migration designs, explain failure modes, and propose a rollback strategy."}],"max_tokens":500}}
```

Upload it with `POST /v1/files` (`file` multipart field, `purpose=batch`), then
send the returned file ID to `POST /v1/batches`:

```json
{"input_file_id":"file-REPLACE","endpoint":"/v1/chat/completions","completion_window":"24h"}
```

Poll `GET /v1/batches/{id}`. When it completes, download
`GET /v1/files/{output_file_id}/content`, and the error file if present.
Successful output retains source order and `custom_id` (`routine`, then
`complex`); one item's failure does not assign its decision to another item.
These prompts illustrate likely economy/quality work, not guaranteed lane labels.
The classifier, current policy, eligibility and upward escalation determine the route.
No checkpoint or internal billing ID is exposed in the output.

In the Route Group's **Selector costs**, explicitly load the desired time window
after spend ingestion. The existing protected
[`GET /ui/api/spend/routing-costs` report](../configuration/model-router-admin.md#optional-recorded-cost-reports)
links each selector cost to its answer. Pending receipts stay pending; real net
savings requires a supplied comparable baseline and penalty evidence.

## Recommended Production Setup

For production, run batch as dedicated worker pods with shared storage and distributed coordination. The API/UI/gateway pods should stay focused on synchronous traffic.

Use this baseline unless your workload is explicitly single-instance or evaluation-only:

```yaml
config:
  general_settings:
    embeddings_batch_enabled: true

    # Shared artifact storage. Required when API and worker pods are split.
    embeddings_batch_storage_backend: s3
    embeddings_batch_s3_bucket: deltallm-batch-artifacts
    embeddings_batch_s3_region: us-east-1
    embeddings_batch_s3_prefix: deltallm/batch-artifacts

    # Production scheduler. Keeps claims small, respects model capacity,
    # prevents tenant starvation, and favors short jobs without starving large jobs.
    embeddings_batch_scheduler_mode: smart_v1
    embeddings_batch_scheduler_shadow_mode: none
    embeddings_batch_scheduler_claim_mode: work_slice
    embeddings_batch_scheduler_strict_model_homogeneity_enabled: true
    embeddings_batch_scheduler_backfill_enabled: true
    embeddings_batch_stale_lease_sweeper_enabled: true

    # Work-slice bounds.
    embeddings_batch_work_claim_max_items: 50
    embeddings_batch_work_claim_max_work_units: 200
    embeddings_batch_work_claim_min_items_for_microbatch: 8

    # Model capacity gate. Keep batch below synchronous gateway capacity.
    embeddings_batch_model_capacity_fraction: 0.25
    embeddings_batch_default_model_max_in_flight: 32
    embeddings_batch_default_model_max_claim_work_units: 128
    embeddings_batch_model_capacity_refresh_seconds: 5
    embeddings_batch_model_capacity_fail_open: false

    # Tenant fairness and size/age scheduling.
    embeddings_batch_scheduler_base_quantum_work_units: 16
    embeddings_batch_scheduler_max_deficit_multiplier: 8
    embeddings_batch_tenant_max_in_flight_work_units: 2000
    embeddings_batch_tenant_max_queued_work_units: 0
    embeddings_batch_tenant_scope_preference: organization,team,api_key,user
    embeddings_batch_scheduler_max_active_flows_per_decision: 100
    embeddings_batch_scheduler_max_candidate_jobs_per_flow: 50
    embeddings_batch_aging_seconds_per_work_unit: 30
    embeddings_batch_max_age_credit_work_units: 1000
    embeddings_batch_min_large_job_claim_interval_seconds: 30
    embeddings_batch_small_job_fast_lane_enabled: false

    # Retry and lease policy.
    embeddings_batch_max_attempts: 5
    embeddings_batch_retry_initial_seconds: 5
    embeddings_batch_retry_max_seconds: 300
    embeddings_batch_retry_multiplier: 2.0
    embeddings_batch_retry_jitter: true
    embeddings_batch_item_lease_seconds: 360
    embeddings_batch_heartbeat_interval_seconds: 15
    embeddings_batch_job_lease_seconds: 120

    # Admission and retention.
    embeddings_batch_max_pending_batches_per_scope: 50
    embeddings_batch_max_items_per_batch: 50000
    embeddings_batch_max_file_bytes: 209715200
    batch_completed_artifact_retention_days: 30
    batch_failed_artifact_retention_days: 14
    batch_metadata_retention_days: 90

batchWorker:
  enabled: true
  replicaCount: 3
  resources:
    requests:
      cpu: 500m
      memory: 1Gi
    limits:
      cpu: 2000m
      memory: 2Gi
  config:
    general_settings:
      embeddings_batch_worker_concurrency: 8
      embeddings_batch_item_claim_limit: 50
```

Production prerequisites:

- PostgreSQL is the durable source of truth for jobs, items, leases, completion records, billing, and scheduler state.
- Redis should be configured for distributed rate limits, max-parallel slots, runtime state, and shared model-group backpressure.
- S3-compatible storage should be used for input, output, and error files whenever more than one pod can serve batch APIs or workers.
- Prometheus should scrape both API and batch-worker pods. In split mode, the Helm chart can render worker-only metrics service discovery.
- Secrets should come from environment variables or Kubernetes Secrets, not literal values in `config.yaml`.

### Traffic-Based Sizing

Start with the profile that matches the expected daily batch volume and provider capacity, then tune with metrics. The effective upstream batch concurrency is approximately:

```text
batchWorker.replicaCount * embeddings_batch_worker_concurrency
```

Keep that number below the provider concurrency you are willing to dedicate to batch. If the same model serves synchronous gateway traffic, reserve enough provider capacity for the gateway and keep `embeddings_batch_model_capacity_fraction` in the `0.10` to `0.25` range. For batch-dedicated models, `0.25` to `0.50` is usually a safer upper bound than letting batch consume the whole model.

| Profile | Expected batch traffic | Worker starting point | Scheduler and capacity starting point | Infrastructure notes |
|---------|------------------------|-----------------------|---------------------------------------|----------------------|
| Small production | Occasional jobs, up to about 50k items/day | `replicaCount: 1`, concurrency `2`-`4`, claim `20` items | `work_claim_max_items: 20`, `work_claim_max_work_units: 80`, model max in-flight `16` | Split workers are still preferred if the API serves real-time traffic. |
| Standard production | Regular team usage, about 50k-1M items/day | `replicaCount: 2`-`3`, concurrency `4`-`8`, claim `50` items | `work_claim_max_items: 50`, `work_claim_max_work_units: 200`, model max in-flight `32` | Use Redis, S3, Prometheus, and enough PostgreSQL connections for API plus workers. |
| High throughput | Sustained multi-tenant traffic or more than 1M items/day | `replicaCount: 4`-`8`, concurrency `8`-`16`, claim `100` items | `work_claim_max_items: 100`, `work_claim_max_work_units: 400`, model max in-flight `64`-`128` | Use a DB pooler or managed proxy, HPA on queue age/depth, worker-only nodes if needed, and per-model capacity data. |

PostgreSQL connection planning is based on process pools, not worker concurrency. A rough reservation is:

```text
(api replicas * db_pool_size) + (batchWorker replicas * db_pool_size) + admin/migration headroom
```

Keep at least 20 percent spare database capacity during normal operation. For example, three worker pods with `db_pool_size=20` reserve about 60 worker-side connections, not `3 * concurrency * 20`.

### Accounting v2 checkpoint writes

With accounting v2, each batch item saves a billing checkpoint before the provider call, and a closing one if the item fails. The write waits up to `embeddings_batch_accounting_write_timeout_seconds` (default 2 seconds). If a try times out or hits a database error, such as a full connection pool, it is tried again, up to 6 tries in all. The pauses between tries grow from about 50 ms to 400 ms, and every try stays inside the same time budget. A write the database answers but refuses (for example, the item is no longer owned by this worker) is never retried. Each worker process runs at most `embeddings_batch_accounting_write_concurrency` writes at once (default: half of `db_pool_size`), so the rest of the pool stays free for lease renewals and result saves. Extra items wait in memory instead of failing.

Sizing rule:

- Keep `embeddings_batch_worker_concurrency` at or below `db_pool_size`. The worker logs a warning at startup when it is higher.
- For more batch throughput, add `batchWorker` replicas instead of raising per-pod concurrency, and check the connection budget above.
- Watch `deltallm_batch_accounting_checkpoint_write_seconds` (by `outcome`) and `deltallm_batch_accounting_checkpoint_write_retries_total`. A rising `unavailable` count means the database or pool is saturated: lower concurrency or claim size, or add database capacity.
- To see which batch work fills the pool (checkpoints, result saves, the outbox, leases, claims or maintenance), use the `deltallm_database_caller_*` metrics. See [Find which work holds the database slots](../deployment/dependency-capacity.md#find-which-work-holds-the-database-slots).

#### Grouping writes while the database is busy

With `embeddings_batch_write_grouping_enabled` on (the default), billing checkpoints and result saves that would wait for a write slot are saved together:

- A write that finds a free slot goes out alone, as before, so a database that keeps up sees no change and no added delay.
- Writes that arrive while every slot is busy wait in order. When a slot frees, the oldest one goes out together with the waiting writes behind it, as one transaction. For example, with 10 slots and 20 items finishing at once, 10 save alone and the other 10 save in one transaction.
- Result saves get as many slots of their own as checkpoint writes (`embeddings_batch_accounting_write_concurrency`, default half of `db_pool_size`), so a burst of saves no longer takes the whole pool.
- A group holds at most 128 checkpoints (1 MiB) or 50 result rows, and never two writes for the same item. It uses the earliest time budget among its members that still leaves time for one attempt.
- Every fence is still checked per item inside the statement, exactly as for a single write. For checkpoints that is the owner, claim epoch, live lease, job state and stored checkpoint. For result saves it is the owner, claim epoch and item status. A group applies to every member or to none. If the database rejects a group, or the group fails for any reason, each member is saved again alone, ahead of newer writes, so each item gets exactly the answer (or error) it would get on its own and one bad row never fails the others.
- The separate lease check before each result save is skipped, because the save checks the same owner, claim epoch and status in its own transaction. A lost item gets `not_owned` from the save instead and follows the same path as before.
- On accounting v2, the completion outbox's sent marks are grouped the same way. At most a quarter of the outbox concurrency (at least 1) mark statements run at once per process, and marks that would wait go out together. Unlike the writes above, each row in a grouped mark keeps its own fence (status, owner, attempt and live lease) and gets its own answer, so a row whose lease ran out never changes another row's result. The money receipt is still accepted before the mark, and no transaction spans the accounting call.

`deltallm_batch_write_group_size` (by `kind`) shows how many writes each statement carried. Turn grouping off to go back to one statement per write, unlimited result saves and sent marks, and the lease check before each save. The setting is read when the batch worker starts, so restart the batch workers after changing it.

#### Completion outbox delivery

The completion outbox worker delivers each finished item's spend and usage. On accounting v2 it finalizes the item's billing receipt, then marks the outbox row sent.

- It keeps up to `embeddings_batch_worker_concurrency` deliveries running (at most 8 per process) and claims more as each one finishes, so one slow delivery no longer holds back the next claim.
- Each claim asks for the free slots plus one more round, up to the claim limit. Claimed rows therefore never wait long for a slot while their lease runs down.
- When a claim comes back short, the queue is empty, and the worker waits the poll interval before claiming again.
- On stop it claims nothing new and finishes the rows it already claimed.

`deltallm_batch_completion_outbox_delivery_lag_seconds` measures the time from an outbox row's creation to its delivery. Watch it, with `deltallm_batch_completion_outbox_failures_total`, to see whether delivery keeps up with completed items.

### Scheduler Modes

Use `smart_v1` for production batch scheduling. It composes:

- work-slice claims, so workers claim bounded item slices instead of monopolizing one large job
- model-capacity gating, so saturated models do not block idle models. When several models have queued work, a free worker goes to the model using the smallest share of its batch capacity (`max_in_flight`), with queue age breaking ties. Jobs on different models therefore run side by side instead of waiting for the oldest model's backlog to drain; a model running alone still gets every worker.
- tenant fair-share, so one tenant cannot dominate a hot model queue
- size and age ranking, so small jobs get better latency while large jobs continue making progress

Use `fifo_v1` only for local evaluation, very small single-tenant deployments, or emergency rollback. Use `slice_v1`, `model_capacity_v1`, and `fair_share_v1` when intentionally disabling part of the production scheduler for a constrained environment.

### Leases, Retries, And Long Provider Calls

`embeddings_batch_item_lease_seconds` should exceed the p99 provider call duration with margin. The default production value of `360` seconds works for most embedding and chat workloads. Keep `embeddings_batch_heartbeat_interval_seconds` well below the lease duration; `15` seconds renews the lease often enough without creating noisy database traffic.

#### Keeping worker slots busy between slices

A worker no longer waits for every item of a slice to finish before it claims the next one. Once every item of its current slice has started and a slot is free, it claims the next slice, which can come from another job. Both slices take their slots from the same pool, so a process still never runs more than `embeddings_batch_worker_concurrency` items (or chat preparations) at once, and two chat slices on the same deployment share that deployment's `max_in_flight`. Finalizing a finished job also runs beside the slices instead of in front of them.

`embeddings_batch_worker_max_slices_in_flight` (default `2`) caps how many slices one process holds. Each slice keeps its own claim, leases, and cancellation, exactly as before, and an embedding slice still sends one grouped upstream call at a time. Set it to `1` to go back to claiming one slice at a time. Only work-slice scheduling (`slice_v1` and later) uses it.

A worker can hold up to `embeddings_batch_worker_concurrency + embeddings_batch_work_claim_max_items` claimed items while a slice's last items run. If a model's batch capacity is tight, keep `work_claim_max_items` close to the worker concurrency so claimed items don't sit waiting.

A worker claims a slice of items at once, and items can wait for a free slot before they start. On every heartbeat the worker renews the leases of all items in its slice that it still owns, in one database statement, so a waiting item is not taken by another worker before it runs. A running item also renews its own lease.

When a slice ends, the worker gives back its items that did not finish. It gives back only items that are still at the claim of that slice. An item of the slice that was retried and claimed again, also by the same worker in another slice, stays with its new claim.

Use `embeddings_batch_max_attempts: 5` for production if provider hiccups are common. Keep retry jitter enabled so workers do not retry the same provider or model group at the same instant.

#### Retries with accounting v2

With accounting v2, an item keeps one billing operation across all its retries. When a provider call fails with a retryable error (timeout, connection error, `429` or `5xx`), the worker leaves that operation open, marks its checkpoint as released, and requeues the item with the normal backoff. The next claim adds one attempt to the same operation instead of opening a new one, so no new funds are reserved.

The item is charged once:

- If a retry succeeds, the charge is the real usage of the successful call. Earlier failed attempts are recorded as unresolved attempts on that charge.
- If the last attempt fails, or the item cannot run again, the operation closes as uncertain and keeps its provisional debit, the same as a single failed call.

An item is retried this way only while all of these hold:

- it has attempts left under `embeddings_batch_max_attempts`
- its operation has attempts left under `accounting_max_provider_attempts` (default 3; the reservation is sized for this many calls)
- the reservation (14 minutes from the first call) outlives the retry delay plus `embeddings_batch_item_lease_seconds`

Otherwise the item fails with `terminal_reason` `accounting_closed` (or `attempts_exhausted`). If a worker crashes during a call, the next claim still closes that operation as uncertain without calling the provider again. Known gap: when a whole embedding micro-batch fails after the provider call, its items are not retried this way yet.

If the checkpoint written before the provider call cannot be saved even after its retries (usually a busy database), the provider is not called. Instead of closing the operation, which would end the item as `accounting_closed`, the worker holds it open for a retry:

- On an item's first claim, the operation is stored with its attempt released, so the next claim adds one attempt under the same funding. That unsent attempt counts toward `accounting_max_provider_attempts`, but it is stored as settled with no usage, so a successful retry is charged exactly and does not list it as an unresolved attempt.
- On a later claim, the new attempt is simply dropped. The stored checkpoint is already released, so nothing is written and the attempt limit is not used up.
- A chat or embedding micro-batch whose checkpoints were not saved is requeued as a whole, with every operation held in one write.

The same limits as above apply. A busy database (for example during a rollout) usually frees up within seconds, so a hold that cannot be saved is written again after 0.5, 1 and 2 seconds. A hold the database answers but refuses, such as for an item this worker no longer owns, is not written again. The one exception is a first claim whose checkpoint was in fact saved but whose reply was lost: the database then refuses the hold because a checkpoint is already stored, so the hold is written once more in place of that checkpoint. If the hold still cannot be saved, the worker falls back to closing the operation, and the item fails as `accounting_closed`. The item's error says `batch_accounting_checkpoint_unavailable` while it waits for its retry.

#### Models without a price on accounting v2

A batch item on a model with no token price is charged $0, the same as a live call to that model:

- Funding reserves only the model's request price, which is $0 when none is set.
- When the item completes, its usage is recorded at $0. The spend row's `unpriced_reason` says why, usually `no_configured_pricing`, and the provider cost is left empty (unknown), not $0.
- If only the customer price is set (for example by a tier) and the deployment itself has no price, the customer is charged as usual and only the provider cost is left empty.

A model with only part of a price (such as an input price and no output price for chat) or an invalid price is still refused before the provider call. An item whose price was found when it was funded but not when it completes still fails with `batch_accounting_checkpoint_unavailable`; it is never recorded at $0.

#### When the gateway's database is busy

A batch item can also fail because DeltaLLM's own database could not take the work, not because the provider failed. These failures (`database_unavailable`, `spend_persistence_unavailable`, `billing_operation_unavailable` and `auth_fallback_unavailable`) have their own retry category, `gateway_busy`, and show as "Gateway busy" instead of "Provider unavailable".

- **Batch workers wait for a connection.** The Helm chart sets `db_capacity_wait_seconds: 2` on batch worker pods, so a database call waits up to 2 seconds for a free connection instead of failing at once. API pods keep `0` so request latency stays bounded. See [Dependency capacity](../deployment/dependency-capacity.md).
- **A short retry that does not use an attempt.** A `gateway_busy` item retries after 1 to 3 seconds, then backs off (targets 6, 12, 24, 48, then 60 seconds, with jitter) if the database stays busy. The first 10 such retries per item give the attempt back, so they do not count toward `embeddings_batch_max_attempts`. After that, or when the failure came after an accounting v2 attempt was funded, the retry uses an attempt as usual. The item's error body records the count in `gateway_busy_retries`.
- **A provider answer is not thrown away.** If the database is busy right after the provider answered, the worker waits and saves the answer again. The first waits are 0.5, 1 and 2 seconds. Longer waits of 4 and then 8 seconds follow, with up to 20% jitter: about 40 seconds in all, and not more than 47 seconds. This is important with accounting v2: an answer that is not saved ends the item, because its next claim cannot call the provider again and fails the item as `accounting_closed` (`batch_accounting_checkpoint_unavailable`). When `embeddings_batch_write_grouping_enabled` is off, the worker first renews the item lease, with waits of 0.5, 1 and 2 seconds.
- **The longer waits stay inside the item lease.** The worker takes a longer wait only while the lease is known to cover the wait plus a reserve. The reserve is a quarter of `embeddings_batch_item_lease_seconds`, and at least one lease renewal and one save at the longest that the database settings let a call take. That is the pool admission wait and the transaction start, the transaction timeout, and then the waits for its last query and its rollback (about 30 seconds with the Helm batch values). With the default 6-minute lease, the quarter of the lease is the larger part. The lease is known from the lease end that the claim returned, less 5 seconds for a difference between the database and worker clocks. If that does not cover the wait, the worker renews the lease and tries the save again at once. It sends that renewal only while the known lease covers the renewal and one save after it. If the renewal fails or is not sent, the worker takes no longer wait. With write grouping, a save can wait for a free save slot, and that wait has no limit. So a save that comes after a longer wait or a renewal does not wait for a save slot: it goes to the database at once, as when write grouping is off. The lease left then covers that one save. The save also checks the owner and claim epoch, so a save after another worker's claim changes nothing.
- **A save that skips a held row is tried again.** The save skips an item row that another statement holds, and then reports that the worker does not own the item. A lease renewal that stops waiting for its result can still hold the row for some seconds. So before the worker gives up the answer, it reads the item again. If the item is still claimed by this worker with the same claim epoch, the worker waits and saves again. If the busy database refuses this read, the worker waits as for a busy save and saves again. Only a read that shows another owner or claim epoch stops the save.
- **A shutdown stops the longer waits.** When the batch worker starts its shutdown drain (30 seconds), it stops a longer wait at once and takes no more of them. The save is tried again, and if it still fails, the item is requeued with the short waits, within the drain. If the drain cancels the save all the same, the answer is lost and the item stays claimed until its lease expires.
- **An unsaved item is given back at once.** If the save still fails, the worker requeues the item. A busy database gets the same 0.5, 1 and 2 second waits there, so the item does not stay claimed until its lease expires (6 minutes by default). With accounting v2, the next claim then fails the item as `accounting_closed` at once.

The stale lease sweeper should stay enabled. It reclaims expired item and job leases even when no worker is currently scanning the affected model group.

## Quick Start

### 1. Enable the feature

```yaml
general_settings:
  embeddings_batch_enabled: true
```

Restart DeltaLLM after changing this setting.

### 2. Upload an input file

Create a JSONL file where each line is a batch request.

Embedding example:

```jsonl
{"custom_id": "doc-1", "url": "/v1/embeddings", "body": {"model": "text-embedding-3-small", "input": "DeltaLLM is an LLM gateway"}}
{"custom_id": "doc-2", "url": "/v1/embeddings", "body": {"model": "text-embedding-3-small", "input": "It supports async batch processing"}}
{"custom_id": "doc-3", "url": "/v1/embeddings", "body": {"model": "text-embedding-3-small", "input": "Batching reduces cost for high-volume workloads"}}
```

Chat completion example:

```jsonl
{"custom_id": "chat-1", "url": "/v1/chat/completions", "body": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Summarize DeltaLLM in one sentence."}]}}
{"custom_id": "chat-2", "url": "/v1/chat/completions", "body": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Write a short support reply."}]}}
```

Upload it:

```bash
curl "$BASE_URL/v1/files" \
  -H "Authorization: Bearer $API_KEY" \
  -F "purpose=batch" \
  -F "file=@input.jsonl"
```

Response:

```json
{
  "id": "file_abc123",
  "object": "file",
  "bytes": 312,
  "filename": "input.jsonl",
  "purpose": "batch",
  "status": "uploaded"
}
```

### 3. Create a batch

```bash
curl "$BASE_URL/v1/batches" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input_file_id": "file_abc123",
    "endpoint": "/v1/embeddings",
    "completion_window": "24h"
  }'
```

DeltaLLM currently supports the OpenAI-compatible `24h` completion window. Missing or null values default to `24h`; other values are rejected.

### Optional terminal webhook

When `batch_webhook_enabled=true`, a batch may include one HTTPS callback configuration. The URL and signing secret are write-only: DeltaLLM encrypts them at rest and returns only whether a webhook is configured.

```bash
curl "$BASE_URL/v1/batches" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input_file_id": "file_abc123",
    "endpoint": "/v1/embeddings",
    "completion_window": "24h",
    "metadata": {"customer_job_id": "job-42"},
    "webhook": {
      "url": "https://receiver.example/deltallm/batches",
      "signing_secret": "replace-with-a-random-secret-at-least-32-bytes"
    }
  }'
```

The create and later batch-read responses contain:

```json
"webhook": {"configured": true}
```

The URL and signing secret are never returned. When the batch reaches `completed`, `failed`, `cancelled`, or `expired`, DeltaLLM stores an immutable event and asynchronously delivers this JSON shape:

```json
{
  "id": "evt_0123456789abcdef",
  "object": "event",
  "type": "batch.completed",
  "created_at": 1786017600,
  "data": {
    "batch": {
      "id": "batch_xyz789",
      "object": "batch",
      "status": "completed",
      "metadata": {"customer_job_id": "job-42"},
      "webhook": {"configured": true}
    }
  }
}
```

The `data.batch` value is the final public batch representation, including caller metadata. Treat the receiver as authorized to receive that batch response. Event types are `batch.completed`, `batch.failed`, `batch.cancelled`, and `batch.expired`.

Every attempt includes these headers:

| Header | Meaning |
|--------|---------|
| `X-DeltaLLM-Event-Id` | Stable event identifier; also sent as `Idempotency-Key` |
| `X-DeltaLLM-Event-Type` | Terminal event type |
| `X-DeltaLLM-Webhook-Attempt` | Current delivery attempt number |
| `X-DeltaLLM-Timestamp` | Unix timestamp used by the signature |
| `X-DeltaLLM-Signature` | `v1=` followed by lowercase HMAC-SHA256 hex |

The signed bytes are `timestamp + b"." + raw_request_body`. Verify the signature before parsing JSON, enforce a timestamp tolerance, and deduplicate by event ID:

```python
import hashlib
import hmac
import time


def verify_deltallm_webhook(
    *,
    raw_body: bytes,
    timestamp_header: str,
    signature_header: str,
    signing_secret: str,
    tolerance_seconds: int = 300,
) -> bool:
    try:
        timestamp = int(timestamp_header)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        return False
    if not signature_header.startswith("v1="):
        return False
    expected = hmac.new(
        signing_secret.encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature_header[3:], expected)
```

Return any `2xx` response to acknowledge delivery. DeltaLLM retries transport/DNS failures, `408`, `425`, `429`, and `5xx` using jittered exponential backoff. A valid `Retry-After` delta or HTTP date is honored up to `batch_webhook_retry_max_seconds`. Other HTTP statuses fail permanently. Delivery failure never changes the batch's terminal status.

Retries preserve the event ID and exact body; signature timestamps and attempt headers change. An operator replay is accepted only for a failed row, preserves the event ID/body, and grants a fresh attempt budget. Receivers must therefore use the event ID as the deduplication key even after acknowledging an event.

### 4. Poll for completion

```bash
curl "$BASE_URL/v1/batches/batch_xyz789" \
  -H "Authorization: Bearer $API_KEY"
```

Watch the `status` field and `request_counts` to track progress:

```json
{
  "id": "batch_xyz789",
  "object": "batch",
  "endpoint": "/v1/embeddings",
  "completion_window": "24h",
  "status": "completed",
  "input_file_id": "file_abc123",
  "output_file_id": "file_out456",
  "error_file_id": null,
  "created_at": 1778064000,
  "expires_at": 1780656000,
  "in_progress_at": 1778064060,
  "completed_at": 1778064300,
  "failed_at": null,
  "expired_at": null,
  "errors": null,
  "request_counts": {
    "total": 3,
    "completed": 3,
    "failed": 0,
    "cancelled": 0,
    "in_progress": 0
  }
}
```

### 5. Download results

```bash
curl "$BASE_URL/v1/files/file_out456/content" \
  -H "Authorization: Bearer $API_KEY" \
  -o output.jsonl
```

Each output line contains the `custom_id` you provided and the endpoint response.

## Input Format

Each line of the input JSONL file must be a JSON object with these fields:

| Field | Required | Description |
|-------|----------|-------------|
| `custom_id` | Yes | Your unique identifier for this request. Used to match input to output. Must be unique within the file. |
| `method` | No | Defaults to `POST`. If provided, it must be `POST`. |
| `url` | No | Must match the batch endpoint if provided. Defaults to the batch endpoint. |
| `body` | Yes | The endpoint request payload. |

Supported batch endpoints:

- `/v1/embeddings`
- `/v1/chat/completions`

For `/v1/embeddings`, the `body` object follows the standard embeddings request format:

| Field | Required | Description |
|-------|----------|-------------|
| `model` | Yes | The model name, e.g. `text-embedding-3-small` |
| `input` | Yes | Text to embed. A string or an array of integers (token IDs). |
| `encoding_format` | No | `float` or `base64` |
| `dimensions` | No | Output dimensionality (if the model supports it) |

For `/v1/chat/completions`, the `body` object follows the non-streaming chat completions request format:

| Field | Required | Description |
|-------|----------|-------------|
| `model` | Yes | The model name, e.g. `gpt-4o-mini` |
| `messages` | Yes | Chat messages using the same shape as synchronous `POST /v1/chat/completions` |
| `stream` | No | Must be omitted or `false`; streaming is not supported in batch |
| `tools` | No | Function tools are accepted; MCP tools are rejected in batch chat for now |
| `response_format` | No | Preserved and forwarded like the synchronous chat endpoint |

> **Note:** All items in a batch should target the same model. The model is inferred from the first item and tracked on the batch record.

## Gateway Policy Enforcement

Batch items are executed with the same gateway policy surface as synchronous traffic. When a batch is created with an API key, the worker reloads the current key context before each executable item and applies:

- model access checks
- hard budget checks for the key, user, team, and organization
- pre-call callbacks, including request transformations
- configured guardrails
- rate limits and max-parallel request controls

Policy failures are isolated to the affected item. Terminal failures, such as model denial, budget exhaustion, guardrail rejection, deleted keys, or expired keys, are written as item failures. Retryable throttling failures, such as rate-limit or max-parallel contention, are requeued using the normal batch retry schedule.

Rate-limit and max-parallel slots are acquired immediately before provider execution and released after the provider attempt finishes. In multi-replica deployments, configure Redis so counters and parallel slots are shared across workers.

## Batch Lifecycle

The worker and admin APIs use the internal lifecycle below:

```
queued --> in_progress --> finalizing --> completed
   |            |                             |
   |            +---> cancelled                |
   |                                          |
   +---> failed                               +--> failed
```

| Status | Description |
|--------|-------------|
| `queued` | Items are ready; waiting for the worker to pick up the job |
| `in_progress` | Worker is executing items against the upstream provider |
| `finalizing` | All items are done; output and error files are being assembled |
| `completed` | Output file is ready for download |
| `failed` | The batch could not be completed (see `error_file_id` for details) |
| `cancelled` | Cancelled by the user or an admin; pending items are skipped |
| `expired` | The batch exceeded its retention window |

A cancel request cancels the batch's unstarted items right away, even when its model has no free capacity or an item is waiting for a retry. Items that are already running finish first. If that first step can't reach the database, the stale lease sweeper finishes it on its next run (every 60 s by default).

Public `/v1/batches` responses use OpenAI-compatible status values and response fields so OpenAI-compatible clients can parse them. Internal `queued` jobs are returned as `validating`, and an `in_progress` or `finalizing` job with a pending cancel request is returned as `cancelling`. Other compatible statuses are returned unchanged. The public response also includes `completion_window`, `errors`, `expires_at`, `failed_at`, and `expired_at` fields.

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/v1/files` | Upload a JSONL input file (`purpose=batch`; other OpenAI purposes are accepted, unknown values return `400`) |
| `GET` | `/v1/files/{file_id}` | Check file metadata and status |
| `GET` | `/v1/files/{file_id}/content` | Download file content (input, output, or error) |
| `POST` | `/v1/batches` | Create a new batch from an uploaded file |
| `GET` | `/v1/batches` | List your batches |
| `GET` | `/v1/batches/{batch_id}` | Get batch status and progress |
| `POST` | `/v1/batches/{batch_id}/cancel` | Request cancellation of a running batch |

`GET /v1/files/{file_id}/content` streams the file from storage in 256 KiB chunks, so a large output doesn't have to fit in API pod memory.
On S3 the download reads the object as it arrives and doesn't spool it to a temp file first.
If storage fails before the first chunk, the request returns an error. If it fails later, the download is cut off and the failure is logged and counted in `deltallm_batch_artifact_failures_total` (`operation="read"`).

Operators also have admin endpoints for monitoring and repair. See [Admin Endpoints](../api/admin.md#batches).

## Configuration

### Minimal Single-Instance Setup

The only required setting is the feature flag:

```yaml
general_settings:
  embeddings_batch_enabled: true
```

All other settings have defaults that work for single-instance deployments with local storage.
For production, use the production setup above instead of the minimal setup.

### Terminal Webhook Delivery

Webhooks require a shared encryption key in addition to the opt-in feature flag:

```yaml
general_settings:
  batch_webhook_enabled: true
  batch_webhook_worker_enabled: true
  batch_webhook_observability_enabled: true
  batch_webhook_encryption_key: os.environ/DELTALLM_BATCH_WEBHOOK_ENCRYPTION_KEY
  batch_webhook_allowed_ports: [443]
  batch_webhook_allow_http: false
```

The destination is validated when the batch is created and resolved again before every attempt. HTTP is denied by default, only configured ports are permitted, and non-global addresses are denied unless covered by `batch_webhook_allowed_private_cidrs`. Cloud metadata endpoints remain denied even when a broad private CIDR is configured. Keep private CIDR exceptions narrow and use them only for controlled internal receivers.

In split deployments, API pods need the encryption key to accept and encrypt configurations but should set both `batch_webhook_worker_enabled=false` and `batch_webhook_observability_enabled=false`. Dedicated `batchWorker` pods use the same key with delivery and observation enabled. Observation has its own lifecycle, so a designated process can continue publishing durable queue state while outbound delivery and garbage collection are paused. Turning `batch_webhook_enabled` off stops new configurations while workers continue draining durable rows. See the [Batch Webhook Rollout Runbook](../deployment/batch-webhook-rollout.md).

### Storage Backend

DeltaLLM stores batch input and output files in a configurable storage backend.

| Backend | Best for | Notes |
|---------|----------|-------|
| `local` | Single-instance deployments, development | Default. Files stored on disk. |
| `s3` | Multi-instance production deployments | Required when running multiple app or worker instances. |

> **Warning:** Local storage is not cluster-safe. If you run multiple DeltaLLM instances, use S3 so all instances can read and write the same artifacts.

#### Local (default)

```yaml
general_settings:
  embeddings_batch_storage_backend: local
  embeddings_batch_storage_dir: .deltallm/batch-artifacts
```

#### S3

```yaml
general_settings:
  embeddings_batch_storage_backend: s3
  embeddings_batch_s3_bucket: my-deltallm-batch-bucket
  embeddings_batch_s3_region: us-east-1
  embeddings_batch_s3_prefix: deltallm/batch-artifacts
  embeddings_batch_s3_access_key_id: os.environ/AWS_ACCESS_KEY_ID
  embeddings_batch_s3_secret_access_key: os.environ/AWS_SECRET_ACCESS_KEY
```

For S3-compatible endpoints (MinIO, R2, etc.), set `embeddings_batch_s3_endpoint_url`:

```yaml
general_settings:
  embeddings_batch_s3_endpoint_url: https://minio.internal:9000
```

### Quotas and Limits

| Setting | Default | Description |
|---------|---------|-------------|
| `embeddings_batch_max_file_bytes` | 52 MB | Maximum upload size for an input file |
| `embeddings_batch_max_items_per_batch` | 10,000 | Maximum items per batch |
| `embeddings_batch_max_line_bytes` | 1 MB | Maximum size of a single JSONL line |
| `embeddings_batch_max_pending_batches_per_scope` | 20 | Maximum active batches per team or API key. Set to `0` to disable. |

### Worker Tuning

The batch worker runs as a background loop that claims jobs and executes items.

| Setting | Default | Description |
|---------|---------|-------------|
| `embeddings_batch_worker_enabled` | `true` | Run the batch worker in this instance |
| `embeddings_batch_completion_outbox_worker_enabled` | `true` | Run the completion outbox worker in this instance |
| `embeddings_batch_poll_interval_seconds` | `1.0` | How often the worker checks for new work when idle |
| `embeddings_batch_worker_concurrency` | `4` | Maximum items one worker process runs at once, across all the slices it runs |
| `embeddings_batch_worker_max_slices_in_flight` | `2` | Work slices one worker process runs at once. The next slice only uses slots the previous slice's last items leave idle. `1` claims one slice at a time |
| `embeddings_batch_runtime_summary_interval_seconds` | `15.0` | How often each worker process refreshes the job, item and queue gauges (1 to 300). The refresh runs beside the claim loop |
| `embeddings_batch_item_claim_limit` | `20` | Maximum items claimed per worker iteration |
| `embeddings_batch_item_buffer_multiplier` | `2` | Multiplier on concurrency for item prefetch. Effective claim limit is `max(item_claim_limit, concurrency * buffer_multiplier)` |
| `embeddings_batch_accounting_write_timeout_seconds` | `2.0` | Accounting v2 only. Time budget for one billing checkpoint write, including the wait for a free write slot and up to 5 retries |
| `embeddings_batch_accounting_write_concurrency` | `0` | Accounting v2 only. Concurrent billing checkpoint writes per worker process; `0` uses half of `db_pool_size` |
| `embeddings_batch_write_grouping_enabled` | `true` | Save billing checkpoints, result saves and accounting v2 outbox sent marks that would wait for a write slot together, and give result saves as many slots as checkpoint writes. `false` restores one statement per write |
| `embeddings_batch_max_attempts` | `3` | Maximum retry attempts for a failed item |
| `embeddings_batch_retry_initial_seconds` | `5` | Initial retry delay for retryable item failures |
| `embeddings_batch_retry_max_seconds` | `300` | Maximum retry delay for retryable item failures, including capped `Retry-After` hints |
| `embeddings_batch_retry_multiplier` | `2.0` | Exponential backoff multiplier between retry attempts |
| `embeddings_batch_retry_jitter` | `true` | Add jitter to spread retries and reduce synchronized retry spikes |
| `embeddings_batch_model_group_backpressure_enabled` | `true` | Temporarily defer model groups that have no healthy deployments |
| `embeddings_batch_model_group_backpressure_min_seconds` | `5` | Minimum model-group deferral duration |
| `embeddings_batch_model_group_backpressure_max_seconds` | `300` | Maximum model-group deferral duration |

### Production Scheduler Settings

| Setting | Recommended production value | Description |
|---------|------------------------------|-------------|
| `embeddings_batch_scheduler_mode` | `smart_v1` | Active scheduler mode for real claims |
| `embeddings_batch_scheduler_shadow_mode` | `none` | Shadow-only scheduler mode; keep disabled in steady state |
| `embeddings_batch_scheduler_claim_mode` | `work_slice` | Bounded item-slice claims instead of whole-job FIFO claims |
| `embeddings_batch_scheduler_strict_model_homogeneity_enabled` | `true` | Reject mixed-model batches so capacity and fairness decisions are well-defined |
| `embeddings_batch_scheduler_backfill_enabled` | `true` | Repair missing scheduler dimensions on older nonterminal jobs |
| `embeddings_batch_work_claim_max_items` | Profile-dependent, usually `50` | Maximum items returned by one work-slice claim |
| `embeddings_batch_work_claim_max_work_units` | Profile-dependent, usually `200` | Maximum estimated work units returned by one claim |
| `embeddings_batch_model_capacity_fraction` | `0.10`-`0.25` for shared models, up to `0.50` for batch-dedicated models | Fraction of known model capacity available to batch |
| `embeddings_batch_model_capacity_fail_open` | `false` | Keep capacity strict when model capacity is unknown; set `true` only when availability matters more than strict batch caps |
| `embeddings_batch_tenant_max_in_flight_work_units` | `2000` as a standard starting point | Prevent one tenant from consuming all in-flight batch capacity |
| `embeddings_batch_scheduler_max_active_flows_per_decision` | `100` | Bound fair-share flow scans per scheduler decision |
| `embeddings_batch_scheduler_max_candidate_jobs_per_flow` | `50` | Bound candidate job reads per active flow |
| `embeddings_batch_small_job_fast_lane_enabled` | `false` | Leave disabled unless rank-based `smart_v1` scheduling is not enough |

Estimated work units come from the request size.
A chat item counts one unit per 512 characters of serialized `messages`, plus one unit per 256 requested output tokens (one unit when no limit is set).
The output limit is `max_completion_tokens` when set, otherwise `max_tokens`, the same order the worker uses when it sends the request.
An embeddings item counts one unit per 256 characters or tokens of `input`.

The scheduler counts a healthy deployment's configured capacity normally.
An unhealthy deployment with an expired cooldown contributes exactly one temporary slot for the shared half-open recovery attempt.
Its full configured capacity becomes available only after that attempt succeeds.

#### Lease and heartbeat

The worker holds leases on jobs and items to prevent duplicate work across instances.

| Setting | Default | Description |
|---------|---------|-------------|
| `embeddings_batch_job_lease_seconds` | `120` | How long a worker holds exclusive access to a job |
| `embeddings_batch_item_lease_seconds` | `360` | How long a worker holds exclusive access to an item |
| `embeddings_batch_heartbeat_interval_seconds` | `15.0` | How often the worker renews its leases |
| `embeddings_batch_finalization_retry_delay_seconds` | `60` | Delay between finalization attempts on failure |

#### Stale lease sweeper

The stale lease sweeper is a lightweight background worker that releases expired job leases and requeues expired in-progress item leases. It is intentionally separate from normal claims, so stale work can recover even when no worker is currently scanning that model group.

| Setting | Default | Description |
|---------|---------|-------------|
| `embeddings_batch_stale_lease_sweeper_enabled` | `true` | Enable periodic stale lease reconciliation |
| `embeddings_batch_stale_lease_sweeper_interval_seconds` | `60` | Normal sweep interval |
| `embeddings_batch_stale_lease_sweeper_failure_interval_seconds` | `30` | Retry interval after a sweep error |
| `embeddings_batch_stale_lease_sweeper_page_size` | `100` | Maximum rows touched per sweep page |
| `embeddings_batch_stale_lease_sweeper_max_rows_per_run` | `500` | Maximum rows touched per sweep run |

### Retention and Cleanup

Completed batches and their artifacts are automatically cleaned up by a background garbage collection loop.

| Setting | Default | Description |
|---------|---------|-------------|
| `batch_completed_artifact_retention_days` | `7` | Days to keep output files for completed batches |
| `batch_failed_artifact_retention_days` | `14` | Days to keep artifacts for failed or cancelled batches |
| `batch_metadata_retention_days` | `30` | Days to keep batch metadata rows in the database |
| `embeddings_batch_gc_enabled` | `true` | Enable background cleanup |
| `embeddings_batch_gc_interval_seconds` | `86400` | How often the cleanup loop runs (default: daily) |
| `embeddings_batch_gc_scan_limit` | `200` | Maximum expired items processed per cleanup pass |
| `embeddings_batch_create_session_cleanup_enabled` | `true` | Enable cleanup for internal staged batch-create artifacts |
| `batch_webhook_delivery_retention_days` | `30` | Days to keep delivered and failed webhook rows for inspection and replay history |
| `batch_webhook_cleanup_max_rows_per_run` | `10000` | Maximum terminal webhook rows deleted per garbage-collection run across bounded pages |

Uploaded files expire after `batch_metadata_retention_days`.
A batch create request that names an expired input file returns `400 Input file has expired`.
Cleanup deletes an expired file's database row first, in one statement that also checks that no batch or batch create request uses the file.
It then deletes the stored object.
Because the row goes first, a file that a new batch starts using at the same moment keeps its content.
If the object delete fails, cleanup logs the storage key and leaves the object in storage.

Webhook cleanup uses the same bounded garbage-collection loop.
It processes short database pages until no eligible rows remain or it reaches `batch_webhook_cleanup_max_rows_per_run`.
Operators can thus set cleanup throughput without one large transaction.
Cleanup removes only `delivered` and `failed` rows older than the webhook retention cutoff.
It never removes queued, retrying, or processing rows.

Webhook rows keep a snapshot of the batch team and organization ownership.
Batch metadata and artifact cleanup can remove the job independently.
Delivery, scoped inspection, and replay remain available through the webhook ownership snapshot.

In Kubernetes production deployments, prefer a split worker deployment over running these worker loops inside every API/UI pod. In shared mode, upper-bound executor pressure is roughly `api replicas * embeddings_batch_worker_concurrency`. In split mode, it becomes `batchWorker replicas * embeddings_batch_worker_concurrency`, so gateway and UI traffic can scale independently from batch throughput.

For the full settings reference, see [Configuration > General](../configuration/general.md#batch-settings).

## Upstream Micro-batching

When the batch worker processes embedding items, it normally sends one upstream HTTP request per item. Embedding micro-batching groups compatible items into a single upstream call, reducing HTTP round trips and improving throughput.

Chat completion batch jobs use bounded per-item concurrency by default.
This is usually the preferred mode for vLLM, which uses continuous batching in its serving engine.
DeltaLLM keeps one canonical response, usage record, and spend record per batch item.

### How it works

1. The worker classifies each item by its input shape and routing parameters
2. Items with the same model, deployment, encoding format, and dimensions are grouped together
3. Groups are sent as a single multi-input embedding request to the upstream provider
4. The response is split back into individual item results
5. Usage and cost are allocated proportionally across grouped items

### Enable micro-batching

Micro-batching is configured per model deployment, not globally. Set `upstream_max_batch_inputs` in `model_info`:

```yaml
model_list:
  - model_name: text-embedding-3-small
    deltallm_params:
      provider: openai
      model: openai/text-embedding-3-small
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: embedding
      upstream_max_batch_inputs: 8
```

This tells the batch worker to group up to 8 compatible items per upstream request.

> **Note:** Embedding micro-batching does not change synchronous `/v1/embeddings` behavior. Chat batching has its own deployment-level `deltallm_params.chat_batching` controls.

### Eligible inputs

Only single-value inputs can be grouped:

| Input shape | Example | Eligible |
|-------------|---------|----------|
| Single string | `"hello world"` | Yes |
| Token ID array | `[1234, 5678]` | Yes |
| String array | `["hello", "world"]` | No |
| Token ID array of arrays | `[[1234], [5678]]` | No |

Multi-input arrays already contain multiple embeddings in a single request and are executed as-is without grouping.

### Failure recovery

If the worker cannot safely divide a grouped upstream response, it executes each item separately.
This prevents one bad grouped response from corrupting the full group.
Retryable overload, timeout, and no-healthy-deployment errors return to the queue first.
Repeated grouped failures reduce future chunk size before the worker uses single-item execution.

When a model group has no healthy deployments, workers create a short backpressure deferral.
With Redis available, worker instances share this deferral.
Newly claimed items for that model group quickly return to the queue without a router or provider call.
If Redis is unavailable, the worker uses a local deferral in process memory.
PostgreSQL retry scheduling remains the authoritative source.

### Production recommendation

For embedding providers with proven multi-input request support, set `upstream_max_batch_inputs` to `4` or `8` first. Use larger values only when provider latency, response shape, and per-item usage accounting stay correct under load. Leave it unset or set it to `1` for providers where multi-input behavior is unknown.

### Usage allocation

When items are grouped, the upstream provider returns usage at the grouped-call level. DeltaLLM allocates per-item usage proportionally based on estimated token weight. Total usage and cost remain consistent; per-item values are approximations.

### Chat batch execution modes

Chat batch execution is configured per deployment in `deltallm_params.chat_batching`:

```yaml
model_list:
  - model_name: support-chat
    deltallm_params:
      provider: vllm
      model: meta-llama/Llama-3.1-8B-Instruct
      api_base: https://vllm.example/v1
      api_key: os.environ/VLLM_API_KEY
      chat_batching:
        mode: concurrent
        max_in_flight: 32
```

Supported modes:

| Mode | Behavior |
|------|----------|
| `disabled` | Execute each item as an ordinary upstream chat request; no microbatch grouping |
| `concurrent` | Execute one upstream chat request per item with worker and deployment concurrency limits |
| `sync_microbatch` | Group compatible chat items into an upstream provider microbatch when a microbatch executor is available |

Missing `chat_batching` config is treated as `concurrent`. The reserved `native_async_batch` mode is not accepted yet.

`max_in_flight` is enforced per worker replica. In Kubernetes, the effective maximum in-flight requests for one deployment is roughly `max_in_flight * worker replica count`, bounded by each replica's worker concurrency. Size these values with provider limits, pod count, and vLLM capacity in mind.

### Sync chat microbatching

Use `sync_microbatch` only for provider adapters that return a result for each input item and exact per-item usage:

```yaml
model_list:
  - model_name: provider-chat
    deltallm_params:
      provider: example_provider
      model: example-chat-model
      api_base: https://provider.example/v1
      api_key: os.environ/PROVIDER_API_KEY
      chat_batching:
        mode: sync_microbatch
        upstream_max_batch_size: 8
        max_total_input_tokens: 32000
        require_homogeneous_params: true
```

The worker groups items only after routing.
The items must have the same deployment, provider, API base, public model, and deployment model.
They must also have the same failover context and non-message request parameters.
If supplied, `require_homogeneous_params` must remain `true`.

The worker uses per-item execution for requests with these features:

- Streaming.
- MCP tools or function tools.
- Complex response formats.
- Multiple choices.
- Token pressure above the configured cap.

Synchronous microbatch calls use the same deployment failover path as ordinary chat requests.
Before a grouped request, the worker examines the served deployment's `chat_batching.mode`, chunk-size limit, token cap, and synchronous microbatch executor.
If the primary fails, a compatible fallback deployment can process the microbatch.
The worker stores the fallback response with that deployment's provider, API base, and pricing metadata.

A served deployment can lack support for the requested microbatch.
Without an earlier retryable microbatch failure that affects health, the worker uses bounded per-item execution.
It does not mark that unsupported deployment unhealthy.
If the primary already had a retryable failure that affects health, the worker returns the chunk to the queue with that failure.
This keeps the primary failure's health and retry behavior.

The worker stores and bills successful microbatch results per item.
If a result lacks per-item usage, that item fails. The worker does not substitute total usage.
For mixed results, it stores successful items and fails or retries only failed items.
The normal batch retry policy classifies structured per-item errors such as `429`, `408`, or `5xx`.
Unstructured provider item errors remain terminal invalid-request failures.

Before persistence, the worker discards provider-owned messages and nested payloads.
Error artifacts and batch UI item responses use an allowlist of stable fields and messages.
This also applies to historical rows.
The `error_file_id` output and batch detail APIs exclude upstream bodies, credentials, URLs, and arbitrary exception text.

Operators can inspect and then irreversibly sanitize provider-owned text already stored on terminal
batch items with `scripts/sanitize_batch_item_errors.py`. Supply the database URL through
`--database-url-file`, run `inspect` first, then run `apply --confirm-sanitize` in bounded pages.
When `has_more=true`, resume with the reported `next_after_item_id` as `--after-item-id`. Take a
database backup before `apply`; the discarded historical text cannot be reconstructed by DeltaLLM.

When an unsupported microbatch splits into individual calls, the chunk runs
them serially within its existing worker slot. Queued items retain their original
claim heartbeats and caller admission; handoff does not consume caller RPM/TPM
again. Completion, failure and cancellation clean up all remaining chunk leases
and refreshers, including items that have not started.

Each split provider attempt rechecks the item's worker/claim-epoch fence after
waiting for deployment capacity and before provider admission. The short renewal
transaction is capped at 250 ms and the remaining shared request/job deadline.
Lost or uncertain ownership stops dispatch locally without marking the provider
unhealthy; existing claim recovery handles the unfinished items. Successful
ordinary microbatching and normal standalone calls do not incur this extra check.

## Monitoring

DeltaLLM exposes Prometheus metrics for batch processing on the configured metrics endpoint.

### Job and queue health

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_jobs` | Gauge | Current job count by status |
| `deltallm_batch_items` | Gauge | Current pending and in-progress item count of queued, in-progress and finalizing jobs |
| `deltallm_batch_oldest_item_age_seconds` | Gauge | Age of the oldest pending or in-progress item of an active job |
| `deltallm_batch_queue_jobs` | Gauge | Current scheduler queue jobs by status, model group, tenant scope, service tier, and size class |
| `deltallm_batch_queue_work_units` | Gauge | Current scheduler queue work units by status, model group, tenant scope, service tier, and size class |
| `deltallm_batch_oldest_job_age_seconds` | Gauge | Age of the oldest batch job by scheduler dimensions |
| `deltallm_batch_worker_saturation_ratio` | Gauge | Worker active/capacity ratio |

The job and item gauges read only active jobs and their open items, so their cost follows active work, not the retained history of finished batches.

Each process that runs the batch worker refreshes the job, item, queue and age gauges every `embeddings_batch_runtime_summary_interval_seconds` (default 15 s), in its own loop beside the claim loop. A worker never waits for this refresh before its next claim. The stale lease sweeper, the scheduler backfill and garbage collection also refresh them after each of their own passes, and admin repair actions refresh them right away. So a gauge can be up to one interval behind the database.

### Scheduler health

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_scheduler_config_info` | Gauge | Active mode, shadow mode, and scheduler config hash reported by each process |
| `deltallm_batch_scheduler_decision_latency_seconds` | Histogram | Scheduler decision latency by mode |
| `deltallm_batch_scheduler_model_skips_total` | Counter | Model groups skipped by capacity or health reason |
| `deltallm_batch_scheduler_flow_skips_total` | Counter | Fair-share flow skips such as lock contention or tenant caps |
| `deltallm_batch_scheduler_fairness_deviation` | Gauge | Difference between actual and expected tenant share for active flows |
| `deltallm_batch_work_claims_total` | Counter | Work-slice claim attempts by result |
| `deltallm_batch_claim_blocked_decisions_total` | Counter | Empty or blocked claim decisions by scheduler mode, bounded reason, and reason category |
| `deltallm_batch_work_claim_latency_seconds` | Histogram | Work-slice claim latency |

### Latency

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_create_latency_seconds` | Histogram | Time to validate and create a batch |
| `deltallm_batch_finalize_latency_seconds` | Histogram | Time to finalize a completed batch |
| `deltallm_batch_item_execution_latency_seconds` | Histogram | Per-item upstream execution time |

### Errors and recovery

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_finalization_retries_total` | Counter | Finalization retries by result |
| `deltallm_batch_artifact_failures_total` | Counter | Storage operation failures by operation and backend |
| `deltallm_batch_completion_outbox_failures_total` | Counter | Completion outbox failures by bounded reason |
| `deltallm_batch_item_reclaims_total` | Counter | Items reclaimed from expired leases |
| `deltallm_batch_stale_lease_sweeper_runs_total` | Counter | Stale lease sweeper runs by status |
| `deltallm_batch_stale_lease_sweeper_rows_total` | Counter | Rows reclaimed, released, skipped, or refreshed by the sweeper |
| `deltallm_batch_stale_lease_sweeper_duration_seconds` | Histogram | Stale lease sweeper duration |
| `deltallm_batch_repair_actions_total` | Counter | Admin repair actions by type and status |
| `deltallm_batch_accounting_checkpoint_write_seconds` | Histogram | Accounting v2 checkpoint write latency by outcome (`saved`, `rejected`, `unavailable`), including the per-process queue |
| `deltallm_batch_accounting_checkpoint_write_retries_total` | Counter | Accounting v2 checkpoint writes retried after a timeout or database error |
| `deltallm_batch_write_group_size` | Histogram | Writes carried by one batch statement, by `kind` (`accounting_checkpoint`, `result_save`, `outbox_sent`). 1 means the write went out alone |
| `deltallm_batch_completion_outbox_delivery_lag_seconds` | Histogram | Seconds from a completion outbox row's creation to its delivery |

### Terminal webhooks

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_webhook_queue_depth` | Gauge | Cluster-wide webhook outbox rows by fixed status |
| `deltallm_batch_webhook_due_depth` | Gauge | Cluster-wide queued, retrying, or expired-processing rows currently eligible for worker processing |
| `deltallm_batch_webhook_oldest_pending_age_seconds` | Gauge | Age of the oldest queued, retrying, or processing event |
| `deltallm_batch_webhook_delivery_attempts_total` | Counter | Attempts by bounded outcome and HTTP status class |
| `deltallm_batch_webhook_delivery_latency_seconds` | Histogram | Full delivery-attempt duration |
| `deltallm_batch_webhook_event_age_seconds` | Histogram | Event age when delivered or permanently failed |
| `deltallm_batch_webhook_retries_scheduled_total` | Counter | Retries by bounded reason |
| `deltallm_batch_webhook_permanent_failures_total` | Counter | Terminal failures by bounded reason |
| `deltallm_batch_webhook_lease_recoveries_total` | Counter | Processing leases recovered for retry or terminalized after final-attempt expiry |
| `deltallm_batch_webhook_replays_total` | Counter | Operator replay requests by bounded result |

Processes with `batch_webhook_observability_enabled=true` emit queue gauges from PostgreSQL snapshots for the full cluster.
The observer does not require the encryption key, delivery worker, or garbage collector.
Queue state remains visible when outbound delivery is disabled or incorrectly configured.

Combine replicated gauges with `max`, not `sum`.
Sum process-local counters and histograms across all processes that produce them.
Delivery, retry, and lease metrics come from workers. Operator replay metrics come from API processes.

Webhook logs contain safe identifiers, status class, bounded reason, attempts, and duration.
They never contain the URL, secret, headers, request body, caller metadata, or encrypted configuration.

### Claim decision logs

Batch workers emit structured `batch_work_claim_decision` records for empty work-slice claims.
They also emit these records when a claim is blocked by these conditions:

- Model capacity or tenant fair-share caps.
- Oversized head items.
- Deferred retries.
- Lease contention.

The production log policy is intentionally bounded:

- `DEBUG` contains every claim decision with the available diagnostic context.
- `INFO` is deduplicated per decision key and includes `suppressed_count` and `window_seconds` when repeated equivalent decisions were suppressed.
- DB-backed diagnostic probes are separately throttled per worker process before repository diagnostics run. Fresh probes set `diagnostic_source="db"`; suppressed probes reuse low-cardinality cached reason context with `diagnostic_source="cached"` or fall back to scheduler context. Failed diagnostic probes use a short retry backoff instead of consuming the full normal probe interval.
- `WARNING` is reserved for diagnostic failures or invalid worker state, not ordinary capacity limits.

Control database probe cost with `embeddings_batch_claim_diagnostics_enabled`, `embeddings_batch_claim_diagnostic_interval_seconds`, and `embeddings_batch_claim_diagnostic_max_keys`.
Use `deltallm_batch_claim_blocked_decisions_total` for alerts and dashboards.
Use structured logs for high-cardinality context: `batch_id`, `tenant_scope_id`, `head_item_work_units`, cap values, and in-flight units.
These fields appear only when fresh scheduler or database context is available.

### Gateway policy

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_policy_allowed_total` | Counter | Batch items that passed gateway policy preflight by endpoint |
| `deltallm_batch_policy_rejected_total` | Counter | Terminal policy rejections by endpoint and reason |
| `deltallm_batch_policy_retryable_failures_total` | Counter | Retryable policy failures by endpoint and reason |
| `deltallm_batch_preflight_latency_seconds` | Histogram | Time spent in batch policy preflight by endpoint and status |

### Micro-batching

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_microbatch_requests_total` | Counter | Grouped upstream requests sent |
| `deltallm_batch_microbatch_inputs_total` | Counter | Total inputs sent through grouped requests |
| `deltallm_batch_microbatch_size` | Histogram | Number of inputs per grouped request (buckets: 1, 2, 4, 8, 16, 32, 64) |
| `deltallm_batch_microbatch_isolation_fallback_total` | Counter | Groups that fell back to single-item execution |
| `deltallm_batch_microbatch_ineligible_items_total` | Counter | Items that could not be grouped, by reason |
| `deltallm_batch_microbatch_requeues_total` | Counter | Grouped retry decisions by retry category and result |
| `deltallm_batch_microbatch_retry_delay_seconds` | Histogram | Grouped retry delay by retry category |
| `deltallm_batch_model_group_deferrals_total` | Counter | Model-group backpressure deferrals by reason |
| `deltallm_batch_model_group_deferred_items_total` | Counter | Items deferred by model-group backpressure by reason |
| `deltallm_batch_model_group_deferral_seconds` | Histogram | Model-group backpressure deferral duration by reason |
| `deltallm_batch_chat_items_executed_total` | Counter | Chat batch items by execution mode and status |
| `deltallm_batch_chat_microbatch_requests_total` | Counter | Sync chat microbatch requests by status |
| `deltallm_batch_chat_microbatch_fallbacks_total` | Counter | Chat microbatch candidates executed per-item by reason |
| `deltallm_batch_chat_microbatch_size` | Histogram | Number of chat items per sync microbatch request |
| `deltallm_batch_chat_provider_latency_seconds` | Histogram | Upstream chat batch worker latency by execution mode and status |

### What to watch

- **`deltallm_batch_oldest_item_age_seconds{status="pending"}`** growing indicates the worker cannot keep up. Increase `worker_concurrency` or add instances.
- **`deltallm_batch_oldest_job_age_seconds{status="queued"}`** growing for one model group indicates model capacity, provider health, or tenant fairness is constraining progress.
- **`deltallm_batch_scheduler_config_info`** should show one active mode, shadow mode, and config hash across worker pods. More than one tuple after a config change means workers have not converged.
- **`deltallm_batch_scheduler_decision_latency_seconds`** p95 should usually stay below `250ms`. Sustained higher latency means scheduler queries or database contention need attention.
- **`deltallm_batch_scheduler_model_skips_total`** with `rpm_exhausted`, `tpm_exhausted`, `no_available_slots`, or `unknown_capacity` explains why a model group is not being claimed.
- **`deltallm_batch_scheduler_flow_skips_total{reason="flow_lock_busy"}`** increasing means too many workers are competing for the same hot model/tier flow decision.
- **`deltallm_batch_work_claims_total{result="empty"}`** increasing while queue depth is low usually means workers are over-provisioned for current traffic.
- **`deltallm_batch_claim_blocked_decisions_total`** breaks empty claims down by reason category such as `tenant_cap`, `model_cap`, `deferred_retry`, `lease_wait`, and `oversized_head_item`. Check `batch_work_claim_decision` logs for the representative batch, tenant, caps, and in-flight units.
- **`deltallm_batch_artifact_failures_total`** indicates storage issues. Check disk space (local) or S3 credentials/connectivity.
- **`deltallm_batch_policy_rejected_total`** increasing usually means current auth, model access, budget, callback, or guardrail policy is rejecting batch items.
- **`deltallm_batch_policy_retryable_failures_total`** increasing usually means batch workers are hitting distributed rate-limit or max-parallel pressure.
- **`deltallm_batch_microbatch_isolation_fallback_total`** increasing after enabling micro-batching may indicate the provider does not handle multi-input requests well. Consider reducing `upstream_max_batch_inputs`.
- **`deltallm_batch_chat_microbatch_fallbacks_total`** increasing after enabling chat `sync_microbatch` means items are being protected by compatibility checks or no executor is available.
- **`deltallm_batch_model_group_deferrals_total`** increasing means workers are seeing temporary model-group unavailability. Check deployment health and router cooldown state.
- **`deltallm_batch_stale_lease_sweeper_runs_total{status="error"}`** should stay at zero. Reclaimed rows should be rare in healthy steady state.
- **`deltallm_batch_webhook_queue_depth{status="failed"}`** should normally remain zero. Inspect the batch detail and receiver health before replaying.
- **`deltallm_batch_webhook_oldest_pending_age_seconds`** growing beyond the maximum retry window indicates worker, network, or receiver pressure.
- **`deltallm_batch_webhook_due_depth`** excludes deliveries waiting for a future retry time. Due depth above zero with no increase in **`deltallm_batch_webhook_delivery_attempts_total`** indicates the webhook worker may be disabled, unhealthy, or unable to claim rows.
- **`deltallm_batch_webhook_lease_recoveries_total`** should be rare; sustained growth indicates worker termination, request timeouts longer than the lease, or database connectivity instability.

## Troubleshooting

### Batch stuck in `in_progress`

The worker holds a lease on the job. If the worker crashes, the lease expires and another worker (or the same worker after restart) reclaims the job.

- Check `deltallm_batch_item_reclaims_total` for lease expiration events
- Check logs for `batch worker iteration failed` errors
- Use the admin UI (**Operations > Batch Jobs**) to inspect item-level status

### Batch stuck in `finalizing`

Finalization assembles the output file from completed items. If storage writes fail, the worker retries after `embeddings_batch_finalization_retry_delay_seconds`.

- Check `deltallm_batch_finalization_retries_total` for repeated failures
- Check `deltallm_batch_artifact_failures_total` for storage backend errors
- Admin API: `POST /ui/api/batches/{batch_id}/cancel` to abandon a permanently stuck batch

### Items keep failing

Each item is retried up to `embeddings_batch_max_attempts` times (default: 3).
Retry delays use exponential backoff starting from `embeddings_batch_retry_initial_seconds`, capped by `embeddings_batch_retry_max_seconds`.
Provider `Retry-After` hints apply to retryable rate-limit and upstream `5xx` failures, including exhausted fallback chains.
The header accepts seconds or an HTTP date.
`embeddings_batch_retry_max_seconds` caps the resulting batch delay.

The router uses provider retry hints for 429 responses only. For provider 5xx failures, same-deployment retry uses the configured local backoff. The OpenAI and Anthropic error responses do not send the provider's 5xx retry hint to clients. Batch retry still uses the hint within its delay cap.
Gateway authentication and output-usage recovery hints stay unchanged.

- Open the batch detail in the admin UI to see per-item error messages
- Common causes: invalid model name, provider rate limits, upstream timeouts
- Budget exhaustion, model access denial, guardrail rejection, deleted keys, and expired keys are terminal and are not retried
- Rate-limit and max-parallel contention are retried until the item reaches `embeddings_batch_max_attempts`
- Check that the model deployment is healthy and reachable
- `batch_accounting_checkpoint_unavailable` (accounting v2): the billing checkpoint could not be saved in time. Check `deltallm_batch_accounting_checkpoint_write_seconds{outcome="unavailable"}` and database load, and keep `embeddings_batch_worker_concurrency` at or below `db_pool_size`. If every item on one model fails this way, check that the model's price is complete (see [Models without a price on accounting v2](#models-without-a-price-on-accounting-v2))

### Terminal webhook is not delivered

- Open **Operations > Batch Jobs** and inspect the redacted Webhook Delivery card.
- Confirm `batch_webhook_worker_enabled=true` on at least one healthy worker and that all API/worker roles use the same encryption key.
- Check queue depth, oldest pending age, retry, permanent-failure, and lease-recovery metrics.
- Review the bounded reason: DNS/transport/timeout errors are retryable, while SSRF policy errors and non-retryable HTTP responses fail permanently.
- Fix the receiver before selecting **Replay delivery**. Replay is available only for failed events and preserves the event ID/body, so the receiver must deduplicate.
- Do not add broad private CIDRs merely to make a rejected destination work; verify the destination and keep SSRF exceptions narrow.

## Known Limitations

- Chat batch supports non-streaming `/v1/chat/completions` requests only
- MCP tools are not supported in chat batch requests yet
- Provider-native async chat batch APIs are not used in this release
- Chat requests default to bounded concurrent execution; synchronous upstream chat micro-batching requires an adapter that supports exact per-item results and usage
- `list[str]` and `list[list[int]]` inputs are not eligible for micro-batching (they are processed individually)
- Local storage is not safe for multi-instance deployments; use S3
- Batch creation is not fully transactional when the database does not support interactive transactions

## Related Pages

- [Proxy Endpoints](../api/proxy.md#batch-endpoints)
- [Admin Endpoints](../api/admin.md#batches)
- [Admin UI: Batch Jobs](../admin-ui/batch-jobs.md)
- [Configuration Reference](../configuration/general.md#batch-settings)
- [Model Deployments](../configuration/models.md#embedding-batch-worker-micro-batching)
