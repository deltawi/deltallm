# Embeddings Batch API

Process large volumes of embedding requests asynchronously. Upload a JSONL file, create a batch, and download results when the job completes. This is ideal when you have thousands of texts to embed and don't need results in real time.

## Quick Start

### 1. Enable the feature

```yaml
general_settings:
  embeddings_batch_enabled: true
```

Restart DeltaLLM after changing this setting.

### 2. Upload an input file

Create a JSONL file where each line is a batch request:

```jsonl
{"custom_id": "doc-1", "url": "/v1/embeddings", "body": {"model": "text-embedding-3-small", "input": "DeltaLLM is an LLM gateway"}}
{"custom_id": "doc-2", "url": "/v1/embeddings", "body": {"model": "text-embedding-3-small", "input": "It supports async batch processing"}}
{"custom_id": "doc-3", "url": "/v1/embeddings", "body": {"model": "text-embedding-3-small", "input": "Batching reduces cost for high-volume workloads"}}
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
  "status": "completed",
  "input_file_id": "file_abc123",
  "output_file_id": "file_out456",
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

Each output line contains the `custom_id` you provided and the embedding response.

## Input Format

Each line of the input JSONL file must be a JSON object with these fields:

| Field | Required | Description |
|-------|----------|-------------|
| `custom_id` | Yes | Your unique identifier for this request. Used to match input to output. Must be unique within the file. |
| `url` | No | Must be `/v1/embeddings` if provided. Defaults to the batch endpoint. |
| `body` | Yes | The embedding request payload, same schema as a synchronous `POST /v1/embeddings` call. |

The `body` object follows the standard embeddings request format:

| Field | Required | Description |
|-------|----------|-------------|
| `model` | Yes | The model name, e.g. `text-embedding-3-small` |
| `input` | Yes | Text to embed. A string or an array of integers (token IDs). |
| `encoding_format` | No | `float` or `base64` |
| `dimensions` | No | Output dimensionality (if the model supports it) |

> **Note:** All items in a batch should target the same model. The model is inferred from the first item and tracked on the batch record.

## Batch Lifecycle

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

### Minimal setup

The only required setting is the feature flag:

```yaml
general_settings:
  embeddings_batch_enabled: true
```

All other settings have sensible defaults that work for single-instance deployments with local storage.

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

#### Lease and heartbeat

The worker holds leases on jobs and items to prevent duplicate work across instances.

| Setting | Default | Description |
|---------|---------|-------------|
| `embeddings_batch_job_lease_seconds` | `120` | How long a worker holds exclusive access to a job |
| `embeddings_batch_item_lease_seconds` | `360` | How long a worker holds exclusive access to an item |
| `embeddings_batch_heartbeat_interval_seconds` | `15.0` | How often the worker renews its leases |
| `embeddings_batch_finalization_retry_delay_seconds` | `60` | Delay between finalization attempts on failure |

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

For the full settings reference, see [Configuration > General](../configuration/general.md#embeddings-batch-settings).

## Upstream Micro-batching

When the batch worker processes embedding items, it normally sends one upstream HTTP request per item. Micro-batching groups compatible items into a single upstream call, reducing HTTP round trips and improving throughput.

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
      model: openai/text-embedding-3-small
      api_key: os.environ/OPENAI_API_KEY
    model_info:
      mode: embedding
      upstream_max_batch_inputs: 8
```

This tells the batch worker to group up to 8 compatible items per upstream request.

> **Note:** Micro-batching only applies to the async batch worker. It does not change synchronous `/v1/embeddings` behavior.

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

### Rollout guidance

1. Leave `upstream_max_batch_inputs` unset or set to `1` to keep micro-batching disabled
2. Start with a small value like `4` or `8`
3. Monitor error rates and the micro-batching metrics (see below)
4. Increase gradually after validating provider behavior

### Usage allocation

When items are grouped, the upstream provider returns usage at the grouped-call level. DeltaLLM allocates per-item usage proportionally based on estimated token weight. Total usage and cost remain consistent; per-item values are approximations.

## Monitoring

DeltaLLM exposes Prometheus metrics for batch processing on the configured metrics endpoint.

### Job and queue health

| Metric | Type | Description |
|--------|------|-------------|
| `deltallm_batch_jobs` | Gauge | Current job count by status |
| `deltallm_batch_items` | Gauge | Current item count by status |
| `deltallm_batch_oldest_item_age_seconds` | Gauge | Age of the oldest item by active status |
| `deltallm_batch_worker_saturation_ratio` | Gauge | Worker active/capacity ratio |

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
| `deltallm_batch_item_reclaims_total` | Counter | Items reclaimed from expired leases |
| `deltallm_batch_repair_actions_total` | Counter | Admin repair actions by type and status |

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

### What to watch

- **`deltallm_batch_oldest_item_age_seconds{status="pending"}`** growing indicates the worker can't keep up. Increase `worker_concurrency` or add instances.
- **`deltallm_batch_artifact_failures_total`** indicates storage issues. Check disk space (local) or S3 credentials/connectivity.
- **`deltallm_batch_microbatch_isolation_fallback_total`** increasing after enabling micro-batching may indicate the provider doesn't handle multi-input requests well. Consider reducing `upstream_max_batch_inputs`.
- **`deltallm_batch_model_group_deferrals_total`** increasing means workers are seeing temporary model-group unavailability. Check deployment health and router cooldown state.

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
- Budget exhaustion is treated as terminal and is not retried
- Check that the model deployment is healthy and reachable

## Known Limitations

- Only `/v1/embeddings` is supported as a batch endpoint
- `list[str]` and `list[list[int]]` inputs are not eligible for micro-batching (they are processed individually)
- Local storage is not safe for multi-instance deployments; use S3
- Batch creation is not fully transactional when the database does not support interactive transactions

## Related Pages

- [Proxy Endpoints](../api/proxy.md#batch-endpoints)
- [Admin Endpoints](../api/admin.md#batches)
- [Admin UI: Batch Jobs](../admin-ui/batch-jobs.md)
- [Configuration Reference](../configuration/general.md#embeddings-batch-settings)
- [Model Deployments](../configuration/models.md#embedding-batch-worker-micro-batching)
