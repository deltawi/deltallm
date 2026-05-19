# Batch API And Production Setup

Process large volumes of embedding or non-streaming chat completion requests asynchronously. Upload a JSONL file, create a batch, and download results when the job completes. This is ideal when you have thousands of requests to run and don't need results in real time.

This page is the main batch reference. It covers the public API, worker behavior, production configuration, scheduler sizing, monitoring, and troubleshooting.

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

### Scheduler Modes

Use `smart_v1` for production batch scheduling. It composes:

- work-slice claims, so workers claim bounded item slices instead of monopolizing one large job
- model-capacity gating, so saturated models do not block idle models
- tenant fair-share, so one tenant cannot dominate a hot model queue
- size and age ranking, so small jobs get better latency while large jobs continue making progress

Use `fifo_v1` only for local evaluation, very small single-tenant deployments, or emergency rollback. Use `slice_v1`, `model_capacity_v1`, and `fair_share_v1` when intentionally disabling part of the production scheduler for a constrained environment.

### Leases, Retries, And Long Provider Calls

`embeddings_batch_item_lease_seconds` should exceed the p99 provider call duration with margin. The default production value of `360` seconds works for most embedding and chat workloads. Keep `embeddings_batch_heartbeat_interval_seconds` well below the lease duration; `15` seconds renews the lease often enough without creating noisy database traffic.

Use `embeddings_batch_max_attempts: 5` for production if provider hiccups are common. Keep retry jitter enabled so workers do not retry the same provider or model group at the same instant.

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
curl http://localhost:8000/v1/files \
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
curl http://localhost:8000/v1/batches \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input_file_id": "file_abc123",
    "endpoint": "/v1/embeddings",
    "completion_window": "24h"
  }'
```

DeltaLLM currently supports the OpenAI-compatible `24h` completion window. Missing or null values default to `24h`; other values are rejected.

### 4. Poll for completion

```bash
curl http://localhost:8000/v1/batches/batch_xyz789 \
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
curl http://localhost:8000/v1/files/file_out456/content \
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

Public `/v1/batches` responses use OpenAI-compatible status values and response fields so OpenAI-compatible clients can parse them. Internal `queued` jobs are returned as `validating`, and an `in_progress` job with a pending cancel request is returned as `cancelling`. Other compatible statuses are returned unchanged. The public response also includes `completion_window`, `errors`, `expires_at`, `failed_at`, and `expired_at` fields.

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/v1/files` | Upload a JSONL input file (`purpose=batch`) |
| `GET` | `/v1/files/{file_id}` | Check file metadata and status |
| `GET` | `/v1/files/{file_id}/content` | Download file content (input, output, or error) |
| `POST` | `/v1/batches` | Create a new batch from an uploaded file |
| `GET` | `/v1/batches` | List your batches |
| `GET` | `/v1/batches/{batch_id}` | Get batch status and progress |
| `POST` | `/v1/batches/{batch_id}/cancel` | Request cancellation of a running batch |

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
| `embeddings_batch_worker_concurrency` | `4` | Maximum concurrent item executions per worker iteration |
| `embeddings_batch_item_claim_limit` | `20` | Maximum items claimed per worker iteration |
| `embeddings_batch_item_buffer_multiplier` | `2` | Multiplier on concurrency for item prefetch. Effective claim limit is `max(item_claim_limit, concurrency * buffer_multiplier)` |
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

In Kubernetes production deployments, prefer a split worker deployment over running these worker loops inside every API/UI pod. In shared mode, upper-bound executor pressure is roughly `api replicas * embeddings_batch_worker_concurrency`. In split mode, it becomes `batchWorker replicas * embeddings_batch_worker_concurrency`, so gateway and UI traffic can scale independently from batch throughput.

For the full settings reference, see [Configuration > General](../configuration/general.md#batch-settings).

## Upstream Micro-batching

When the batch worker processes embedding items, it normally sends one upstream HTTP request per item. Embedding micro-batching groups compatible items into a single upstream call, reducing HTTP round trips and improving throughput.

Chat completion batch jobs default to bounded per-item concurrency. For vLLM, this is usually the preferred mode because vLLM performs continuous batching inside the serving engine while DeltaLLM keeps one canonical response, usage record, and spend record per batch item.

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

If a grouped upstream request returns a response shape that cannot be split safely, the worker falls back to executing each item individually. This ensures a single bad grouped response does not corrupt the entire group. Retryable overload, timeout, and no-healthy-deployment errors are requeued first; repeated grouped failures reduce the future chunk size before falling back to single-item execution.

When a model group has no healthy deployments, workers also create a short-lived backpressure deferral. With Redis available, this deferral is shared across worker instances so newly claimed items for that model group are quickly requeued without calling the router or upstream provider. If Redis is unavailable, the worker falls back to a local in-process deferral and still uses Postgres retry scheduling as the source of truth.

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

The worker groups only after routing, and only when items share the same deployment, provider, API base, public model, deployment model, failover context, and non-message request parameters. If `require_homogeneous_params` is provided, it must remain `true`. Requests with streaming, MCP tools, function tools, complex response formats, multiple choices, or token pressure above the configured cap fall back to per-item execution.

Sync microbatch calls use the same deployment failover path as ordinary chat requests. Before sending a grouped request to any served deployment, the worker checks that deployment's own `chat_batching.mode`, chunk-size limit, token cap, and sync microbatch executor. If the primary deployment fails and a fallback deployment also supports the requested sync microbatch, the fallback response is persisted with the fallback deployment's provider, API base, and pricing metadata. If the served deployment cannot run the requested sync microbatch and no earlier retryable health-affecting microbatch failure occurred, the worker degrades to bounded per-item execution without marking that deployment unhealthy. If the primary deployment already failed with a retryable health-affecting error before failover reaches an unsupported deployment, the worker requeues the chunk using the primary failure so health and retry semantics are preserved.

Successful microbatch results are persisted and billed per item. If a provider result is missing per-item usage, that item fails instead of using aggregate usage. Mixed success and failure results persist successful items and fail or retry only the affected failed items. Structured per-item provider errors with retryable status codes such as `429`, `408`, or `5xx` are classified by the normal batch retry policy; unstructured provider item errors remain terminal invalid-request failures.

## Monitoring

DeltaLLM exposes Prometheus metrics for batch processing on the configured metrics endpoint.

### Job and queue health

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_jobs` | Gauge | Current job count by status |
| `deltallm_batch_items` | Gauge | Current item count by status |
| `deltallm_batch_oldest_item_age_seconds` | Gauge | Age of the oldest item by active status |
| `deltallm_batch_queue_jobs` | Gauge | Current scheduler queue jobs by status, model group, tenant scope, service tier, and size class |
| `deltallm_batch_queue_work_units` | Gauge | Current scheduler queue work units by status, model group, tenant scope, service tier, and size class |
| `deltallm_batch_oldest_job_age_seconds` | Gauge | Age of the oldest batch job by scheduler dimensions |
| `deltallm_batch_worker_saturation_ratio` | Gauge | Worker active/capacity ratio |

### Scheduler health

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_scheduler_config_info` | Gauge | Active mode, shadow mode, and scheduler config hash reported by each process |
| `deltallm_batch_scheduler_decision_latency_seconds` | Histogram | Scheduler decision latency by mode |
| `deltallm_batch_scheduler_model_skips_total` | Counter | Model groups skipped by capacity or health reason |
| `deltallm_batch_scheduler_flow_skips_total` | Counter | Fair-share flow skips such as lock contention or tenant caps |
| `deltallm_batch_scheduler_fairness_deviation` | Gauge | Difference between actual and expected tenant share for active flows |
| `deltallm_batch_work_claims_total` | Counter | Work-slice claim attempts by result |
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

- **`deltallm_batch_oldest_item_age_seconds{status="pending"}`** growing indicates the worker can't keep up. Increase `worker_concurrency` or add instances.
- **`deltallm_batch_oldest_job_age_seconds{status="queued"}`** growing for one model group indicates model capacity, provider health, or tenant fairness is constraining progress.
- **`deltallm_batch_scheduler_config_info`** should show one active mode, shadow mode, and config hash across worker pods. More than one tuple after a config change means workers have not converged.
- **`deltallm_batch_scheduler_decision_latency_seconds`** p95 should usually stay below `250ms`. Sustained higher latency means scheduler queries or database contention need attention.
- **`deltallm_batch_scheduler_model_skips_total`** with `rpm_exhausted`, `tpm_exhausted`, `no_available_slots`, or `unknown_capacity` explains why a model group is not being claimed.
- **`deltallm_batch_scheduler_flow_skips_total{reason="flow_lock_busy"}`** increasing means too many workers are competing for the same hot model/tier flow decision.
- **`deltallm_batch_work_claims_total{result="empty"}`** increasing while queue depth is low usually means workers are over-provisioned for current traffic.
- **`deltallm_batch_artifact_failures_total`** indicates storage issues. Check disk space (local) or S3 credentials/connectivity.
- **`deltallm_batch_policy_rejected_total`** increasing usually means current auth, model access, budget, callback, or guardrail policy is rejecting batch items.
- **`deltallm_batch_policy_retryable_failures_total`** increasing usually means batch workers are hitting distributed rate-limit or max-parallel pressure.
- **`deltallm_batch_microbatch_isolation_fallback_total`** increasing after enabling micro-batching may indicate the provider doesn't handle multi-input requests well. Consider reducing `upstream_max_batch_inputs`.
- **`deltallm_batch_chat_microbatch_fallbacks_total`** increasing after enabling chat `sync_microbatch` means items are being protected by compatibility checks or no executor is available.
- **`deltallm_batch_model_group_deferrals_total`** increasing means workers are seeing temporary model-group unavailability. Check deployment health and router cooldown state.
- **`deltallm_batch_stale_lease_sweeper_runs_total{status="error"}`** should stay at zero. Reclaimed rows should be rare in healthy steady state.

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
Provider `Retry-After` hints are honored for retryable rate-limit failures, but are also capped by `embeddings_batch_retry_max_seconds`.

- Open the batch detail in the admin UI to see per-item error messages
- Common causes: invalid model name, provider rate limits, upstream timeouts
- Budget exhaustion, model access denial, guardrail rejection, deleted keys, and expired keys are terminal and are not retried
- Rate-limit and max-parallel contention are retried until the item reaches `embeddings_batch_max_attempts`
- Check that the model deployment is healthy and reachable

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
