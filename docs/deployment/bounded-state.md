# Bounded in-memory state

DeltaLLM keeps several bounded stores and queues in memory on the accounting
v2 and batch paths. Each one refuses new work when it is full, so a store that
never empties turns into a permanent "Gateway busy" or 503 on that pod until
the pod restarts. This page lists every such store, what empties it, and which
test proves that it empties.

The rule for every store: **after load ends, it returns to its idle level
within 5 minutes, on every pod, without a restart.**

## Inventory

| Store | Where | Limit | Emptied by | Can another pod empty it? | Metric | Drain test |
|---|---|---|---|---|---|---|
| Local receipt store | API and batch pods, accounting v2 `local_journal` | `accounting_finalization_max_pending_bytes` (8 MB, at most about 1,000 receipts) and `accounting_finalization_max_pending` (8,192) | The finalization on the pod that issued the receipt; the pod's release worker when the terminal journal shows another pod finalized it, or 60 s after its grant expires in the database | **Yes.** Another pod's finalization is found in the journal (checked 10 s after issue, then every 10 s) | `deltallm_accounting_local_receipt_fill_ratio`, `deltallm_accounting_local_receipts`, `deltallm_accounting_local_receipt_releases_total` | `test_bounded_state_accounting_postgres.py`, `test_accounting_local_release.py` |
| Local grant cursor store | API and batch pods, `local_journal` | `accounting_local_max_subjects` (1,024) and `accounting_reservation_max_pending_bytes` (8 MB) | The return worker returns unused permits after the dispatch window ends. It drops a remaining cursor 60 s after the grant's recovery deadline. Database recovery owns the unreturned amount. | Each pod's return worker drains its own cursors | None | `test_accounting_local_returns_postgres.py` |
| Reservation microbatch queue | Every pod running accounting v2 | `accounting_reservation_max_pending` (4,096) and `accounting_reservation_max_pending_bytes` (8 MB) | The queue's own batch task; callers that leave early free their space | Not needed: per process | `deltallm_accounting_queue_depth{queue="reservation"}`, `deltallm_accounting_queue_retained_bytes` | `test_bounded_state_drain.py`, `test_bounded_state_accounting_postgres.py` |
| Finalization microbatch queue | Every pod running accounting v2 | `accounting_finalization_max_pending` (8,192) and `accounting_finalization_max_pending_bytes` (8 MB) | Same as above | Not needed: per process | `deltallm_accounting_queue_depth{queue="finalization"}`, `deltallm_accounting_queue_retained_bytes` | Same as above |
| Pre-issued permit bank | Not used | `max_subjects`, `max_retained_bytes` | | | `deltallm_accounting_permit_bank_*` | None: no runtime builds it. Only its `PermitSubject` type is used, by the local stores |
| Batch checkpoint write slots | Batch pods, native billing | `embeddings_batch_accounting_write_concurrency` (0 means half of `db_pool_size`) | Each write frees its slot when it ends, fails or is cancelled | Not needed: per process | `deltallm_batch_write_group_size{kind="accounting_checkpoint"}` (writes only, no queue gauge) | `test_bounded_state_drain.py` |
| Batch result save slots | Batch pods with `embeddings_batch_write_grouping_enabled` | `batch_result_save_concurrency` (from `db_pool_size`) | Same as above | Not needed: per process | `deltallm_batch_write_group_size{kind="result_save"}` | `test_bounded_state_drain.py` |
| Outbox sent-mark slots | Batch pods, native billing | `sent_mark_concurrency` of the completion outbox worker (0 means no limit) | Same as above | Not needed: per process | `deltallm_batch_write_group_size{kind="outbox_sent"}` | `test_bounded_state_drain.py` (same slot owner as the two rows above) |
| Batch execution slots | Batch pods | `embeddings_batch_worker_concurrency` | Each work unit frees its slot when it ends, fails or is cancelled; each slice closes its tracker | Not needed: per process | `deltallm_batch_worker_saturation_ratio` | `test_bounded_state_drain.py` |
| Batch completion outbox | PostgreSQL table, any batch pod | Rows are durable; each claim holds a lease (`lease_seconds`, 30 s) | The pod that claimed the row marks it sent, retrying or failed | **Yes.** A row whose lease expires goes back to the queue and any pod claims it | `deltallm_batch_completion_outbox_delivery_lag_seconds`, `deltallm_batch_completion_outbox_failures_total` | `tests/batch/test_bounded_state_outbox_postgres.py` |
| Audit content buffer | Pods with durable audit ingestion; only accounting v2 requests fill it | 2,000 items and about 128 MiB of body characters (waiting and in flight); one send is at most 64 MiB and one statement at most 1 MiB; text and embeddings requests are kept as the JSON text their request checks already made, counted by its length, when it is at most 1 MiB and its separators plus a quarter of its digits are at most 49,152 (a short parse in the flusher), and a larger request is copied by the request checks before the provider call; bodies are measured and copied from at most 16,384 values each, and a larger body is replaced by a placeholder; floats count 24 characters, and an integer over 1,024 bits is stored as a placeholder; the flusher writes each record as JSON in parts of at most 1,024 values and sends that text; later changes to the request's objects cannot grow what is held | The pod's flusher task, every second or at 500 items. A failed send is dropped, not retried. A full buffer drops new content and never fails the request | Not needed: per process | `deltallm_audit_content_buffer_items`, `deltallm_audit_content_dropped_total` | `test_audit_content_buffer.py` |
| Audit stream reply memory | Pods that stream text replies whose content can be stored | 64 MiB of reply characters for all streams of the pod, and the audit payload limit minus 16 KiB for one stream | Each stream gives its share back when its reply body is built, or when the stream object is freed after a failure or cancellation. A full budget cuts the reply (`truncated`) and never fails the stream | Not needed: per process | None | `test_stream_reply.py` |
| Gateway preflight capacity | Redis lease set shared by API pods, when `gateway_preflight_capacity_enabled` is on | `gateway_preflight_global_max_parallel` (300), `gateway_preflight_org_max_parallel` (100) | The response's end releases its leases; a lease that is never released expires after `gateway_preflight_lease_ttl_seconds` (30 s) | **Yes.** Leases expire in Redis, so a dead pod's capacity comes back | None | `test_bounded_state_preflight_redis.py` |

### How the drain tests work

- Tests that need a database or Redis start several "pods" (separate runtimes or
  clients) against one real PostgreSQL or Redis, load them at once, and check
  that every store returns to idle **while the pods keep running**. A store that
  only empties on shutdown counts as a leak, because prod pods run for days.
- Stores that live in one process (queues and slots) are overloaded in one
  process, with failures and cancellations mixed in. The test then checks that
  the store is idle and still serves new work.

## Known gaps

### Local receipts finalized on another pod (fixed)

A receipt used to be removed only when the pod that issued it finalized the
operation. Batch finalization goes through the completion outbox, and any batch
pod can claim an outbox row, so pod A kept receipts for work pod B finalized.
A's store filled after about 1,000 batch items, and every new reservation on A
then failed with `queue_full` ("Gateway busy") until A restarted.

Each API and batch pod now runs a release worker. It checks receipts older than
10 seconds against the terminal journal and frees the ones another pod
finalized. It also frees a receipt 60 seconds after its grant expires in the
database, which covers operations that are never finalized on any pod: after
that expiry the journal refuses to finalize them, and the recovery worker owns
them. The cross-pod drain test now passes without a marker. See
[Local issue proofs](accounting-v2.md#local-issue-proofs) for the metrics.

### Stores with no fill metric

The cursor store, the write slots, preflight capacity and the audit stream
reply memory have no gauge for how full they are. A full reply memory only cuts
audit replies, which show `truncated: true`. The receipt store has one,
`deltallm_accounting_local_receipt_fill_ratio`; the others are listed for the
alerting work.

### Outbox rows held by a dead pod are not counted as pending

`count_pending_completion_outbox` counts only `queued` and `retrying` rows. A
row held by a pod that died stays `processing` until its lease expires (30 s),
so it is not in that count during that time. The drain test checks that such
rows are delivered exactly once by the other pods, and that the dead pod's late
answer is refused.

## Adding a new bounded store

Before merging a new in-memory store or queue on the accounting or batch path:

1. Add a row to the table above.
2. Make sure the work that empties it cannot land on a different pod, or give
   it a cross-pod owner (a durable row, a lease that expires, or a release
   driven by the journal).
3. Add a drain test that loads it from several pods and checks it returns to
   idle while they keep running.
4. Export a gauge for how full it is, so an alert can fire before it is full.
