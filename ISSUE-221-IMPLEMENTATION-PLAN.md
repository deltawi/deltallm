# Issue 221 Implementation Plan: Batch Claim Decision Observability

Issue: https://github.com/deltawi/deltallm/issues/221

Worktree: `.worktrees/issue-221-batch-claim-logs`

Branch: `issue-221-batch-claim-logs`

Base: `origin/main` at `f3e673de6628332926351d64d53c86233b41e039`

## Issue Summary

Observed worker logs are too generic when the batch scheduler cannot make progress. Operators must query the database manually to understand whether the worker is blocked by tenant caps, model capacity, oversized head items, unavailable worker/model slots, deferred retries, or lease waits.

Acceptance requires every empty or blocked claim to expose:

- batch ID
- model group
- tenant
- head item work units
- cap values
- in-flight units
- reason

## Current State

Relevant files:

- `src/batch/worker.py`
  - `_process_once_work_slice()` records empty and claimed work-claim metrics.
  - It logs only successful claims with `batch work slice claimed id=...`.
  - When `claim is None`, it increments `deltallm_batch_claim_empty_jobs_total` with `await self._empty_work_claim_reason()`, but does not log the diagnostic context.
  - `_claim_next_work_slice()` records model capacity and fair-share claim results through the resolver, but most blocked paths do not emit worker logs.
  - `_capacity_empty_claim_result()` calls `repository.diagnose_model_group_work_claim_empty(...)`, but only receives a reason string.
  - `_empty_work_claim_reason()` calls `repository.diagnose_empty_work_claim()`, but only receives a reason string.

- `src/batch/repositories/job_repository.py`
  - `claim_next_work()` and `_claim_next_work_with_client()` contain the actual work-slice claim SQL.
  - `claim_next_fair_share_work()` returns `BatchFairShareClaimResult` with useful result and flow context.
  - `_select_scheduler_flow()` already knows fair-share skip reasons such as `tenant_in_flight_full`, `oversized_head_item`, `insufficient_deficit`, and `flow_scan_limit_reached`.
  - `diagnose_model_group_work_claim_empty()` distinguishes model capacity full, work-unit capacity full, oversized head item, and no runnable items, but returns only a string.
  - `diagnose_empty_work_claim()` distinguishes no work, no pending items, future retries, and locked work, but returns only a string.

- `src/batch/scheduling/model_capacity.py`
  - `BatchModelCapacitySnapshot` already exposes model capacity fields: max in-flight, max claim work units, available slots, available work units, queued work units, in-flight items/work units, and reason.
  - `select_model_groups()` records skip metrics for ineligible snapshots, but the worker does not log those blocked snapshots when no eligible model is returned.

- `src/metrics/batch.py`
  - Existing bounded metrics:
    - `deltallm_batch_work_claims_total{result,claim_mode}`
    - `deltallm_batch_claim_empty_jobs_total{reason}`
    - `deltallm_batch_scheduler_model_claims_total{model_group,result}`
    - `deltallm_batch_scheduler_model_skips_total{model_group,reason}`
    - `deltallm_batch_scheduler_flow_skips_total{reason}`
  - The metric labels are appropriately bounded for `reason`. Do not add batch ID or tenant IDs as metric labels.

## Reason Taxonomy

Use bounded reason strings in metrics and logs. Add a `reason_category` log field for the issue's operator-facing categories without breaking existing metric names.

Recommended mappings:

| Category | Existing/New Reason Values |
| --- | --- |
| tenant cap | `tenant_in_flight_full` |
| model cap | `capacity_full_after_lock`, `capacity_work_units_full_after_lock`, `no_available_slots`, `no_available_work_units`, `rpm_exhausted`, `tpm_exhausted`, `unknown_capacity` |
| oversized head item | `oversized_head_item` |
| no workers/model free | `no_available_slots`, `no_healthy_deployments`, `unknown_model_group`, `health_state_unavailable`, `router_state_unavailable` |
| deferred retry | `not_before_future` with category `deferred_retry` |
| lease wait | `job_terminal_or_leased`, `all_items_locked` with category `lease_wait` |
| no queue work | `no_available_work`, `no_pending_items`, `no_runnable_items_after_selection` |

If changing metric reason labels is acceptable, normalize `not_before_future` to `deferred_retry` and `all_items_locked`/`job_terminal_or_leased` to `lease_wait`. Otherwise, keep existing reason labels and add `reason_category` only to structured logs.

## Target Design

### Logging Level Policy

The implementation should be production-safe by separating "record every decision" from "print every decision at production log level."

- Metrics record every empty or blocked claim decision with bounded labels.
- `DEBUG` logs include every claim decision with the available structured diagnostic context for development and short-term production investigations.
- `INFO` logs should be rate-limited and deduplicated for normal production. Emit the first occurrence for a decision key, then periodic summaries with `suppressed_count` and `window_seconds`.
- `WARNING` is for diagnostic failures, repository/transaction capability gaps, invalid claim state, or sustained no-progress thresholds. Normal capacity limits, tenant caps, deferred retries, and lease waits are operational states, not warnings by default.
- `ERROR` remains for invariants or data corruption, such as a claimed row missing a parseable lease.

Add a small worker-local log limiter keyed by stable operational dimensions such as:

- `decision`
- `reason`
- `reason_category`
- `claim_mode`
- `scheduler_mode`
- `model_group`
- `service_tier`
- `tenant_scope_type`
- `diagnostic_source`

Do not include high-cardinality fields such as tenant ID, batch ID, item ID, or head item size in the limiter key. Keep the limiter bounded with TTL cleanup and a maximum key count. A reasonable default is a 60 second INFO window per key. This avoids flooding production logs during idle polling while still surfacing repeated blocked states with summarized counts.

### DB Diagnostic Probe Policy

The logging limiter does not protect the database if DB-backed diagnostics run before log emission. Add a separate worker-local diagnostic probe limiter before calls to `diagnose_empty_work_claim_context()` and `diagnose_model_group_work_claim_empty_context()`.

- Probe key for global empty work: `("empty", claim_mode)`.
- Probe key for model-capacity selected-empty work: `("model_capacity_empty", model_group, service_tier)`.
- Cache only low-cardinality diagnostic fields: reason, reason category, model group, and service tier.
- Do not cache representative batch IDs, tenant IDs, head item work units, defer timestamps, or lease timestamps.
- Mark logs with `diagnostic_source`: `db`, `cached`, `fallback`, or `scheduler_context`.
- Failed diagnostic probes should keep any low-cardinality cache and retry after a short bounded backoff rather than suppressing DB diagnostics for the full normal interval.
- Expose `embeddings_batch_claim_diagnostics_enabled`, `embeddings_batch_claim_diagnostic_interval_seconds`, and `embeddings_batch_claim_diagnostic_max_keys` so production operators can disable or slow DB diagnostic enrichment under DB pressure.

### 1. Add a typed diagnostic payload

Add a small dataclass, preferably in `src/batch/models.py` near `BatchWorkClaim` or in a new `src/batch/claim_diagnostics.py` module:

```python
@dataclass(frozen=True, slots=True)
class BatchClaimDecisionDiagnostic:
    reason: str
    reason_category: str
    diagnostic_source: str | None = None
    diagnostic_probe_suppressed_count: int | None = None
    batch_id: str | None = None
    model_group: str | None = None
    service_tier: str | None = None
    tenant_scope_type: str | None = None
    tenant_scope_id: str | None = None
    head_item_work_units: int | None = None
    max_items: int | None = None
    max_work_units: int | None = None
    capacity_max_in_flight_items: int | None = None
    capacity_max_in_flight_work_units: int | None = None
    available_in_flight_items: int | None = None
    available_work_units: int | None = None
    in_flight_items: int | None = None
    in_flight_work_units: int | None = None
    tenant_max_in_flight_work_units: int | None = None
    tenant_in_flight_work_units: int | None = None
    queued_work_units: int | None = None
    active_flow_count: int | None = None
    scanned_flow_count: int | None = None
    deferred_until: datetime | None = None
    lease_expires_at: datetime | None = None
```

Add a helper such as `to_log_extra(worker_id, claim_mode, scheduler_mode, decision_scope)` that returns only JSON/logging-safe primitive values. This keeps log call sites small and prevents accidental high-cardinality metric labels.

Keep old string-returning repository methods for compatibility:

- `diagnose_empty_work_claim()` returns `diagnostic.reason`
- `diagnose_model_group_work_claim_empty()` returns `diagnostic.reason`

Add new context methods:

- `diagnose_empty_work_claim_context() -> BatchClaimDecisionDiagnostic`
- `diagnose_model_group_work_claim_empty_context(...) -> BatchClaimDecisionDiagnostic`

### 2. Emit structured worker logs for empty work-slice claims

In `BatchExecutorWorker._process_once_work_slice()`:

1. Replace the direct `increment_batch_claim_empty_job(reason=await self._empty_work_claim_reason())` call with:
   - `diagnostic = await self._empty_work_claim_diagnostic()`
   - `increment_batch_claim_empty_job(reason=diagnostic.reason)`
   - `self._log_claim_decision("empty", diagnostic, latency_seconds=claim_latency_seconds, active_mode=active_mode)`
2. `_log_claim_decision(...)` should always make the full diagnostic available at `DEBUG`, but only emit production `INFO` when the limiter allows it. The INFO record must include `suppressed_count` when repeated equivalent decisions were suppressed in the current window.
3. Include at minimum:
   - `event="batch_work_claim_decision"`
   - `decision="empty"`
   - `worker_id`
   - `claim_mode`
   - `scheduler_mode`
   - `reason`
   - `reason_category`
   - `batch_id`
   - `model_group`
   - `service_tier`
   - `tenant_scope_type`
   - `tenant_scope_id`
   - `head_item_work_units`
   - `max_items`
   - `max_work_units`
   - `capacity_max_in_flight_items`
   - `capacity_max_in_flight_work_units`
   - `available_in_flight_items`
   - `available_work_units`
   - `in_flight_items`
   - `in_flight_work_units`
   - `tenant_max_in_flight_work_units`
   - `tenant_in_flight_work_units`
   - `queued_work_units`
   - `claim_latency_seconds`

Use both `extra={...}` and a short key-value message for compatibility with current text log consumers. The helper chooses `DEBUG` for every event and throttled `INFO` for production:

```python
logger.log(
    level,
    "batch work claim decision decision=%s reason=%s reason_category=%s batch_id=%s model_group=%s tenant_scope_type=%s tenant_scope_id=%s head_item_work_units=%s",
    "empty",
    diagnostic.reason,
    diagnostic.reason_category,
    diagnostic.batch_id,
    diagnostic.model_group,
    diagnostic.tenant_scope_type,
    diagnostic.tenant_scope_id,
    diagnostic.head_item_work_units,
    extra=diagnostic.to_log_extra(...),
)
```

### 3. Upgrade model-group empty diagnostics

In `BatchJobRepository.diagnose_model_group_work_claim_empty_context(...)`:

Extend the existing query to return a representative head item and capacity details:

- `batch_id`
- `tenant_scope_type`
- `tenant_scope_id`
- `service_tier`
- `model_group`
- `head_item_work_units`
- `in_flight_items`
- `in_flight_work_units`
- `capacity_max_in_flight_items`
- `capacity_max_in_flight_work_units`
- `max_work_units`

Use the current reason logic, but populate diagnostic fields:

- `capacity_full_after_lock` when in-flight items are at or above the item cap.
- `capacity_work_units_full_after_lock` when in-flight work units are at or above the work-unit cap.
- `oversized_head_item` when a runnable head item exists but does not fit.
- `no_runnable_items_after_selection` when the selected model/tier has no runnable head item.
- `empty_after_selection` as fallback.

Important SQL detail: keep this diagnostic bounded. Do not scan all jobs. Use the same model group and service tier filters as the claim path, sample a deterministic set of the oldest candidate jobs before joining items, and pick one representative head item with deterministic order.

### 4. Upgrade global empty diagnostics

In `BatchJobRepository.diagnose_empty_work_claim_context()`:

Extend the existing bounded `candidate_jobs LIMIT 100` probe to include a representative job and head item:

- candidate job fields: `batch_id`, `scheduling_model_group`, `tenant_scope_type`, `tenant_scope_id`, `service_tier`, `queue_entered_at`, `lease_expires_at`
- head item fields: `estimated_work_units`, `status`, `not_before_at`, `lease_expires_at`

Populate context for:

- `not_before_future`: include `deferred_until` from the earliest blocked pending item.
- lease wait cases: include item or job `lease_expires_at`.
- `all_items_locked`: include the representative runnable-but-locked job/item when available.
- `no_pending_items`: include the representative active job if one exists.

Keep the current query bounded and preserve current behavior for the string-returning wrapper.

### 5. Log model capacity blocked selections

When active mode uses model capacity and `select_model_groups()` returns no selections, the worker currently falls through to legacy fallback. Add a log before fallback that explains why all model groups were skipped.

Implementation options:

1. Add `last_selection_snapshots()` or `last_blocked_snapshots()` to `BatchModelCapacityResolver`.
2. Or have `select_model_groups()` return an object with `selections` and `snapshots`.

Prefer option 1 to minimize call-site churn:

- Store the snapshots from the last `select_model_groups()` call on the resolver.
- Add a worker helper `_model_capacity_blocked_diagnostics(resolver, max_items, max_work_units)` that converts up to a small bounded number of blocked snapshots into diagnostics.
- Log one diagnostic per blocked model group, bounded to the first 5 by oldest queue time to avoid log floods.

Required fields from `BatchModelCapacitySnapshot`:

- `model_group`
- `service_tier`
- `reason`
- `max_in_flight_items`
- `max_claim_work_units`
- `available_in_flight_items`
- `available_work_units`
- `queued_work_units`
- `in_flight_items`
- `in_flight_work_units`
- `rpm_remaining`
- `tpm_remaining`
- `healthy_deployments`
- `backpressure_until`
- `capacity_source`

This covers model cap, no free model capacity, no healthy deployments, and model-group deferrals.

### 6. Log fair-share blocked decisions

In `_claim_next_work_slice()`, when `claim_next_fair_share_work(...)` returns `claim is None`, log a blocked decision before fallback or continuation.

Use fields from `BatchFairShareClaimResult` and `BatchSchedulerFlowRecord`:

- result/reason
- `flow.next_batch_id`
- `flow.model_group`
- `flow.service_tier`
- `flow.tenant_scope_type`
- `flow.tenant_scope_id`
- `flow.next_item_work_units`
- `flow.queued_work_units`
- `flow.in_flight_work_units`
- `tenant_max_in_flight_work_units`
- `active_flow_count`
- `total_in_flight_work_units`
- `recommended_batch_id`
- `recommended_size_class`
- `recommended_scheduler_rank`
- `recommended_policy_reason`

Special cases:

- `flow_lock_busy`: log reason category `lease_wait` or `scheduler_lock_contention`, with model group and service tier even if there is no flow.
- `tenant_in_flight_full`: log reason category `tenant_cap` with tenant cap and tenant in-flight values.
- `oversized_head_item`: log the flow's `next_item_work_units` as `head_item_work_units`.
- `flow_scan_limit_reached`: log `active_flow_count` and configured `tenant_fair_share_max_active_flows_per_decision`.

### 7. Log selected model capacity empty claims

When a model-capacity selection is attempted and `claim_next_work(...)` returns `None`, `_capacity_empty_claim_result()` currently returns only a reason. Change it to return a `BatchClaimDecisionDiagnostic`.

Suggested worker changes:

- Add `_capacity_empty_claim_diagnostic(snapshot, max_items, max_work_units)`.
- Keep `_capacity_empty_claim_result(...)` as a compatibility wrapper returning `.reason` if tests or shadow code still call it.
- Log the diagnostic immediately after recording `resolver.record_claim_result(...)` for empty selected models.

Fields from the selection snapshot plus repository diagnostic should be merged. Repository fields win for head item and batch/tenant; snapshot fields win for caps, available capacity, and in-flight counts.

### 8. Improve successful claim log context

Enhance the existing successful log in `_process_once_work_slice()`:

- Keep the message `batch work slice claimed ...` for compatibility.
- Do not increase successful-claim log volume beyond the current behavior. This change should only add structured `extra` fields to the existing log, or route it through the same limiter if the team decides to lower successful claims to `DEBUG` later.
- Add `extra` fields:
  - `event="batch_work_claim_decision"`
  - `decision="claimed"`
  - `worker_id`
  - `claim_id`
  - `batch_id`
  - `model_group`
  - `service_tier`
  - `tenant_scope_type`
  - `tenant_scope_id`
  - `item_count`
  - `claimed_work_units`
  - `claim_mode`
  - `scheduler_mode`

This is not strictly required for blocked claims, but it makes claimed versus empty decisions comparable.

### 9. Metrics changes

Every empty or blocked claim decision must be counted in metrics. Do not add high-cardinality labels such as batch ID, tenant ID, or exact model names to new empty-claim metrics unless they already exist on a bounded model metric.

Recommended metrics approach:

1. Keep `deltallm_batch_claim_empty_jobs_total{reason}` for empty claims.
2. Keep `deltallm_batch_scheduler_model_skips_total{model_group,reason}` because it already exists and model group is already an accepted label in this area.
3. Keep `deltallm_batch_scheduler_flow_skips_total{reason}`.
4. Optionally add `deltallm_batch_claim_blocked_decisions_total{claim_mode,reason,reason_category}` if a single cross-path metric is useful. If adding it, expose it through `src/metrics/__init__.py` and test in `tests/test_batch_metrics.py`.

The issue asks for metrics/logs to explain claim decisions. Prometheus should carry counts and bounded reasons; detailed batch and tenant context belongs in structured logs.

### 10. Documentation

Update:

- `docs/features/batching.md`
- `docs/deployment/batch-scheduler-rollout.md`

Document:

- New log event name: `batch_work_claim_decision`
- Stable log fields and their meanings.
- Reason taxonomy and category mapping.
- Which Prometheus metrics to use when debugging:
  - `deltallm_batch_claim_empty_jobs_total`
  - `deltallm_batch_scheduler_model_skips_total`
  - `deltallm_batch_scheduler_flow_skips_total`
  - optional `deltallm_batch_claim_blocked_decisions_total`

## Detailed Implementation Sequence

1. Add `BatchClaimDecisionDiagnostic` and helper functions.
   - Include a `reason_category_for_claim_decision(reason: str) -> str` helper.
   - Include `to_log_extra(...)` for logging.
   - Add unit tests for reason-category mapping.

2. Add repository context methods.
   - Implement `BatchJobRepository.diagnose_empty_work_claim_context()`.
   - Implement `BatchJobRepository.diagnose_model_group_work_claim_empty_context(...)`.
   - Keep existing string-returning methods as wrappers.
   - Add facade methods to `src/batch/repository.py`.

3. Wire empty work-slice logging in `BatchExecutorWorker`.
   - Add `_empty_work_claim_diagnostic()`.
   - Add `_log_claim_decision(...)`.
   - Replace the current empty reason call with diagnostic logging plus existing metric increment.

4. Wire model-capacity selected-empty diagnostics.
   - Add `_capacity_empty_claim_diagnostic(...)`.
   - Merge snapshot and repository context.
   - Log selected-empty decisions before moving to the next model group.

5. Wire no-selection model-capacity diagnostics.
   - Store the latest snapshots in `BatchModelCapacityResolver`.
   - Add a bounded worker log when no eligible selections are returned.
   - Preserve existing legacy fallback behavior.

6. Wire fair-share blocked diagnostics.
   - Log `BatchFairShareClaimResult` when result is not `claimed`.
   - Include flow context when present.
   - Include model/service context for `flow_lock_busy` even without a selected flow.

7. Enhance successful claim logs.
   - Add structured `extra` fields to the existing successful claim log.
   - Do not change the message prefix unless tests require it.

8. Add or update metrics only after logging is in place.
   - Prefer existing metrics unless a unified blocked-decision counter is needed.
   - Keep labels bounded.

9. Update docs.
   - Add a short debugging table with reasons, categories, and next actions.
   - Mention that detailed batch/tenant context is in logs, not Prometheus labels.

10. Run focused validation.
   - Unit tests first.
   - Then lint on touched files.
   - DB integration tests only if local DB is available.

## Test Plan

### Unit tests: worker logs

Add tests in `tests/test_batch_worker.py`:

- Empty claim logs diagnostic context.
  - Fake repository returns a `BatchClaimDecisionDiagnostic` for `diagnose_empty_work_claim_context()`.
  - Assert `caplog.records[0].reason`, `batch_id`, `model_group`, tenant fields, and `head_item_work_units`.
  - Assert `increment_batch_claim_empty_job` still receives the bounded reason.

- Empty diagnostic failure falls back to `no_available_work`.
  - Existing fallback test should also assert a fallback log exists with reason `no_available_work`.

- Model capacity selected model returns no claim.
  - Fake capacity resolver returns two selections.
  - First selection returns `None` and repository diagnostic returns `oversized_head_item` with batch/tenant/head work units.
  - Second selection returns a claim.
  - Assert a blocked log for the first selection and a claimed log for the second.

- Model capacity no eligible selections.
  - Fake resolver records skipped snapshots such as `no_available_slots` and `no_healthy_deployments`.
  - Assert logs contain cap values, queued/in-flight fields, and reason.
  - Assert legacy fallback behavior remains unchanged.

- Fair-share tenant cap.
  - Fake repository returns `BatchFairShareClaimResult(claim=None, result="tenant_in_flight_full", flow=...)`.
  - Assert log contains tenant scope, tenant cap, in-flight units, model group, and head item work units.

- Fair-share flow lock busy.
  - Fake repository returns `flow_lock_busy`.
  - Assert log contains model group, service tier, reason, and worker ID.

### Unit tests: repository diagnostics

Add or update tests in `tests/test_batch_repository.py`:

- `diagnose_empty_work_claim_context()` returns representative batch, tenant, model group, head item work units, and `deferred_until` for future retry.
- `diagnose_empty_work_claim()` still returns the reason string for compatibility.
- `diagnose_model_group_work_claim_empty_context()` returns:
  - `oversized_head_item` with `batch_id` and `head_item_work_units`
  - `capacity_full_after_lock` with in-flight item count and item cap
  - `capacity_work_units_full_after_lock` with in-flight work units and work-unit cap
  - `no_runnable_items_after_selection` with model/service context
- Existing SQL-shape tests still pass and remain bounded.

### Unit tests: metrics

Update `tests/test_batch_metrics.py` only if adding a new metric.

If no new metric is added, assert existing metrics continue to expose new reason values where applicable.

### Optional DB integration

If a local DB is available, extend `tests/test_batch_db_integration.py` with one integration case:

- Create a queued model-capacity batch where the head item exceeds max claim work units.
- Run one worker claim attempt.
- Assert no claim is made, empty reason is `oversized_head_item`, and the diagnostic context has the expected batch ID and head work units.

## Validation Commands

Focused:

```bash
uv run pytest tests/test_batch_worker.py -q
uv run pytest tests/test_batch_repository.py -q
uv run pytest tests/test_batch_metrics.py -q
uv run ruff check src/batch src/metrics tests/test_batch_worker.py tests/test_batch_repository.py tests/test_batch_metrics.py
```

If DB services are running:

```bash
uv run pytest tests/test_batch_db_integration.py -q
```

Broader confidence before PR:

```bash
uv run pytest tests/test_batch_worker.py tests/test_batch_repository.py tests/test_batch_metrics.py tests/test_batch_model_capacity.py tests/test_batch_backpressure.py -q
uv run ruff check .
```

## Risks And Guardrails

- Avoid increasing normal production log volume. Record every decision through metrics and DEBUG diagnostics, and use the INFO limiter for repeated equivalent blocked states.
- Do not run DB diagnostics just because a log may be emitted. DB-backed diagnostics must be separately throttled before repository calls.
- Do not add batch IDs or tenant IDs as Prometheus labels. Put them in structured logs only.
- Keep diagnostic SQL bounded. Avoid full queue scans.
- Preserve existing string-returning diagnostic methods so current tests and external callers do not break.
- Shadow scheduler decisions should be logged with `shadow_mode` or `decision_scope="shadow"` if they become visible. Do not make shadow-only blocks look like active worker blocks.
- Keep reason values bounded and documented. Avoid embedding exception text or raw model names in `reason`.

## Definition Of Done

- Every empty or blocked active work-slice claim is counted in metrics with a bounded reason.
- Structured claim decision context is available at `DEBUG`, and production `INFO` logs are rate-limited/deduplicated with `suppressed_count`.
- DB-backed diagnostic probes are bounded per worker process, configurable, and can be disabled.
- Structured diagnostics include reason, batch ID when a representative job exists, model group, service tier, tenant scope, head item work units, cap values, and in-flight units where applicable.
- Existing metrics continue to record bounded reasons, and any new metric uses only bounded labels.
- Unit tests cover empty claim, selected model empty claim, no eligible model capacity selection, tenant cap, model cap, oversized head item, deferred retry, and lease wait.
- Docs explain how to use the logs and metrics to debug slow batches.
