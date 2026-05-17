from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from prisma import Prisma

import src.batch.request_validation as batch_request_validation_module
from src.batch.models import BatchJobRecord
from src.batch.repository import BatchRepository
from src.batch.scheduling import BatchModelCapacitySelection, BatchModelCapacitySnapshot
from src.batch.service import BatchService
from src.batch.storage import LocalBatchArtifactStorage
from src.metrics import get_prometheus_registry
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.models.responses import UserAPIKeyAuth


class FileUpload:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.filename = path.name
        self._handle = path.open("rb")

    async def read(self, size: int = -1) -> bytes:
        return await asyncio.to_thread(self._handle.read, size)

    async def close(self) -> None:
        await asyncio.to_thread(self._handle.close)


class NoopBudgetService:
    async def check_budgets(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs


class NoopSpendTrackingService:
    async def log_spend(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs

    async def log_request_failure(self, **kwargs) -> None:  # noqa: ANN003
        del kwargs


class NoopPassiveHealthTracker:
    async def record_request_outcome(
        self,
        deployment_id: str,
        success: bool,
        error: str | None = None,
        *,
        exc: Exception | None = None,
    ) -> None:
        del deployment_id, success, error, exc


class NoopRouterStateBackend:
    async def increment_usage_counters(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs


@dataclass
class BatchLoadMeasurement:
    items: int
    workers: int
    worker_concurrency: int
    item_claim_limit: int
    input_file_bytes: int
    upload_latency_seconds: float
    batch_create_latency_seconds: float
    processing_latency_seconds: float
    total_latency_seconds: float
    throughput_items_per_second: float
    peak_rss_mib: float
    job_status: str
    runtime_summary_after_create: dict[str, float]
    runtime_summary_after_processing: dict[str, float]
    request_counts: dict[str, int]
    scheduler_report: dict[str, float | int | None]


@dataclass(frozen=True)
class SchedulerLoadMetricSnapshot:
    flow_lock_busy: float
    legacy_lock_busy: float
    candidate_snapshot_reads: float
    in_flight_snapshot_reads: float
    scheduler_skips: float
    work_claim_attempts: float
    scheduler_decisions: float
    reclaims: float
    duplicate_completion_rejections: float
    decision_latency_buckets: dict[float, float]
    candidate_snapshot_latency_buckets: dict[float, float]
    in_flight_snapshot_latency_buckets: dict[float, float]


class StaticModelCapacityResolver:
    def __init__(
        self,
        *,
        model_group: str,
        service_tier: str,
        max_in_flight_items: int,
        max_work_units: int,
    ) -> None:
        self.model_group = model_group
        self.service_tier = service_tier
        self.max_in_flight_items = max(1, int(max_in_flight_items))
        self.max_work_units = max(1, int(max_work_units))

    async def select_model_groups(
        self,
        *,
        max_items: int,
        max_work_units: int,
    ) -> list[BatchModelCapacitySelection]:
        selected_items = max(1, min(int(max_items), self.max_in_flight_items))
        selected_work_units = max(1, min(int(max_work_units), self.max_work_units))
        snapshot = BatchModelCapacitySnapshot(
            model_group=self.model_group,
            service_tier=self.service_tier,
            max_in_flight_items=self.max_in_flight_items,
            max_claim_work_units=selected_work_units,
            available_in_flight_items=selected_items,
            available_work_units=selected_work_units,
            rpm_remaining=None,
            tpm_remaining=selected_work_units,
            healthy_deployments=1,
            backpressure_until=None,
            reason=None,
            capacity_source="load-script",
            queued_jobs=1,
            queued_work_units=selected_work_units,
        )
        return [
            BatchModelCapacitySelection(
                snapshot=snapshot,
                max_items=selected_items,
                max_work_units=selected_work_units,
            )
        ]

    def record_selection(self, snapshot: BatchModelCapacitySnapshot) -> None:
        del snapshot

    def record_claim_result(self, *, model_group: str, result: str) -> None:
        del model_group, result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure embedding batch create/process throughput and memory.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="Postgres DATABASE_URL to use")
    parser.add_argument("--items", type=int, default=1000, help="Number of embedding items to generate")
    parser.add_argument("--workers", type=int, default=1, help="Number of batch workers to run concurrently")
    parser.add_argument("--worker-concurrency", type=int, default=8, help="Batch worker per-process concurrency")
    parser.add_argument("--item-claim-limit", type=int, default=8, help="Batch worker item claim limit")
    parser.add_argument(
        "--scheduler-mode",
        choices=("fifo_v1", "slice_v1", "model_capacity_v1", "fair_share_v1", "smart_v1"),
        default="fifo_v1",
        help="Scheduler mode to exercise during processing",
    )
    parser.add_argument("--service-tier", default="standard", help="Scheduler service tier to exercise; currently standard only")
    parser.add_argument("--model", default="m1", help="Model identifier to place in each batch item")
    parser.add_argument("--input-text", default="hello", help="Input text for each embedding request")
    parser.add_argument("--force", action="store_true", help="Allow non-local or non-deltallm database targets")
    return parser


def peak_rss_mib() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return float(raw) / (1024.0 * 1024.0)
    return float(raw) / 1024.0


async def ensure_batch_schema(db: Any) -> None:
    rows = await db.query_raw("SELECT to_regclass('public.deltallm_batch_job')::text AS name")
    if not rows or dict(rows[0]).get("name") is None:
        raise RuntimeError("Batch tables are missing. Run `uv run prisma db push --schema=./prisma/schema.prisma` first.")


def validate_database_url(database_url: str, *, force: bool) -> None:
    parsed = urlparse(database_url)
    host = str(parsed.hostname or "").strip().lower()
    database_name = parsed.path.lstrip("/")
    if force:
        return
    if host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("Refusing to run against a non-local database host without --force")
    if not database_name.startswith("deltallm"):
        raise RuntimeError("Refusing to run against a database outside the deltallm local/dev naming pattern without --force")


async def cleanup_run_data(db: Any, *, created_by_api_key: str) -> None:
    await db.execute_raw(
        """
        DELETE FROM deltallm_batch_item
        WHERE batch_id IN (
            SELECT batch_id
            FROM deltallm_batch_job
            WHERE created_by_api_key = $1
        )
        """,
        created_by_api_key,
    )
    await db.execute_raw(
        """
        DELETE FROM deltallm_batch_job
        WHERE created_by_api_key = $1
        """,
        created_by_api_key,
    )
    await db.execute_raw(
        """
        DELETE FROM deltallm_batch_file
        WHERE created_by_api_key = $1
        """,
        created_by_api_key,
    )


def write_input_file(path: Path, *, items: int, model: str, input_text: str) -> int:
    total_bytes = 0
    with path.open("wb") as handle:
        for idx in range(1, items + 1):
            line = json.dumps(
                {
                    "custom_id": f"item-{idx}",
                    "url": "/v1/embeddings",
                    "body": {"model": model, "input": input_text},
                }
            ).encode("utf-8") + b"\n"
            handle.write(line)
            total_bytes += len(line)
    return total_bytes


def build_worker_app(*, model: str) -> Any:
    deployment_obj = SimpleNamespace(
        deployment_id="load-deployment",
        deltallm_params={"model": model, "api_base": "http://localhost"},
        input_cost_per_token=0.001,
        output_cost_per_token=0.0,
        model_info={"batch_input_cost_per_token": 0.0005, "batch_output_cost_per_token": 0.0},
    )

    class Router:
        def resolve_model_group(self, requested_model: str) -> str:
            return requested_model

        async def select_deployment(self, model_group: str, request_context: dict) -> str:
            del model_group, request_context
            return "load-deployment"

        def require_deployment(self, model_group: str, deployment: str):  # noqa: ANN001
            del model_group, deployment
            return deployment_obj

    class Failover:
        async def execute_with_failover(
            self,
            *,
            primary_deployment,
            model_group,
            execute,
            return_deployment=False,
            **kwargs,
        ):
            del model_group, kwargs
            data = await execute(primary_deployment)
            if return_deployment:
                return data, primary_deployment
            return data

    return SimpleNamespace(
        state=SimpleNamespace(
            router=Router(),
            failover_manager=Failover(),
            spend_tracking_service=NoopSpendTrackingService(),
            budget_service=NoopBudgetService(),
            passive_health_tracker=NoopPassiveHealthTracker(),
            router_state_backend=NoopRouterStateBackend(),
        )
    )


async def drain_worker(worker: BatchExecutorWorker) -> None:
    while True:
        did_work = await worker.process_once()
        if not did_work:
            return


async def drain_workers(workers: list[BatchExecutorWorker], *, max_rounds: int) -> None:
    idle_rounds = 0
    for _ in range(max(1, max_rounds)):
        results = await asyncio.gather(*(worker.process_once() for worker in workers))
        if any(results):
            idle_rounds = 0
            continue
        idle_rounds += 1
        if idle_rounds >= 2:
            return
    raise RuntimeError("Batch workers did not drain all available work before max rounds")


def request_counts(job: BatchJobRecord) -> dict[str, int]:
    return {
        "total": job.total_items,
        "completed": job.completed_items,
        "failed": job.failed_items,
        "cancelled": job.cancelled_items,
        "in_progress": job.in_progress_items,
    }


def _metric_sample_value(metric_name: str, labels: dict[str, str] | None = None) -> float:
    expected_labels = labels or {}
    value = 0.0
    for family in get_prometheus_registry().collect():
        for sample in family.samples:
            if sample.name != metric_name:
                continue
            if any(str(sample.labels.get(key)) != expected for key, expected in expected_labels.items()):
                continue
            value += float(sample.value)
    return value


def _metric_gauge_max(metric_name: str) -> float:
    values: list[float] = []
    for family in get_prometheus_registry().collect():
        for sample in family.samples:
            if sample.name == metric_name:
                values.append(float(sample.value))
    return max(values) if values else 0.0


def _histogram_buckets(
    metric_name: str,
    labels: dict[str, str] | None = None,
) -> dict[float, float]:
    expected_labels = labels or {}
    buckets: dict[float, float] = {}
    for family in get_prometheus_registry().collect():
        for sample in family.samples:
            if sample.name != f"{metric_name}_bucket":
                continue
            if any(str(sample.labels.get(key)) != expected for key, expected in expected_labels.items()):
                continue
            raw_bound = str(sample.labels.get("le") or "")
            if raw_bound == "+Inf":
                buckets[float("inf")] = buckets.get(float("inf"), 0.0) + float(sample.value)
                continue
            try:
                bound = float(raw_bound)
            except ValueError:
                continue
            buckets[bound] = buckets.get(bound, 0.0) + float(sample.value)
    return buckets


def _subtract_metric_buckets(
    current: dict[float, float],
    baseline: dict[float, float],
) -> dict[float, float]:
    return {
        bound: max(0.0, current.get(bound, 0.0) - baseline.get(bound, 0.0))
        for bound in set(current) | set(baseline)
    }


def _histogram_quantile_from_buckets(
    buckets: dict[float, float],
    quantile: float,
) -> float | None:
    total = buckets.get(float("inf"), max(buckets.values(), default=0.0))
    if total <= 0:
        return None
    target = total * max(0.0, min(float(quantile), 1.0))
    for bound in sorted(bound for bound in buckets if bound != float("inf")):
        if buckets[bound] >= target:
            return bound
    return None


def _histogram_count(metric_name: str) -> float:
    return _metric_sample_value(f"{metric_name}_count")


def scheduler_load_metric_snapshot() -> SchedulerLoadMetricSnapshot:
    return SchedulerLoadMetricSnapshot(
        flow_lock_busy=_metric_sample_value(
            "deltallm_batch_scheduler_flow_skips_total",
            {"reason": "flow_lock_busy"},
        ),
        legacy_lock_busy=_metric_sample_value(
            "deltallm_batch_scheduler_flow_skips_total",
            {"reason": "lock_busy"},
        ),
        candidate_snapshot_reads=_metric_sample_value(
            "deltallm_batch_scheduler_snapshot_reads_total",
            {"kind": "candidate"},
        ),
        in_flight_snapshot_reads=_metric_sample_value(
            "deltallm_batch_scheduler_snapshot_reads_total",
            {"kind": "in_flight"},
        ),
        scheduler_skips=_metric_sample_value("deltallm_batch_scheduler_flow_skips_total"),
        work_claim_attempts=_metric_sample_value("deltallm_batch_work_claims_total"),
        scheduler_decisions=_histogram_count("deltallm_batch_scheduler_decision_latency_seconds"),
        reclaims=_metric_sample_value("deltallm_batch_item_reclaims_total"),
        duplicate_completion_rejections=_metric_sample_value(
            "deltallm_batch_duplicate_completion_rejections_total"
        ),
        decision_latency_buckets=_histogram_buckets(
            "deltallm_batch_scheduler_decision_latency_seconds"
        ),
        candidate_snapshot_latency_buckets=_histogram_buckets(
            "deltallm_batch_scheduler_snapshot_latency_seconds",
            {"kind": "candidate"},
        ),
        in_flight_snapshot_latency_buckets=_histogram_buckets(
            "deltallm_batch_scheduler_snapshot_latency_seconds",
            {"kind": "in_flight"},
        ),
    )


def scheduler_load_report(
    *,
    processed_items: int,
    baseline: SchedulerLoadMetricSnapshot | None = None,
) -> dict[str, float | int | None]:
    current = scheduler_load_metric_snapshot()
    baseline = baseline or SchedulerLoadMetricSnapshot(
        flow_lock_busy=0.0,
        legacy_lock_busy=0.0,
        candidate_snapshot_reads=0.0,
        in_flight_snapshot_reads=0.0,
        scheduler_skips=0.0,
        work_claim_attempts=0.0,
        scheduler_decisions=0.0,
        reclaims=0.0,
        duplicate_completion_rejections=0.0,
        decision_latency_buckets={},
        candidate_snapshot_latency_buckets={},
        in_flight_snapshot_latency_buckets={},
    )
    flow_lock_busy = max(0.0, current.flow_lock_busy - baseline.flow_lock_busy)
    legacy_lock_busy = max(0.0, current.legacy_lock_busy - baseline.legacy_lock_busy)
    candidate_snapshot_reads = max(
        0.0,
        current.candidate_snapshot_reads - baseline.candidate_snapshot_reads,
    )
    in_flight_snapshot_reads = max(
        0.0,
        current.in_flight_snapshot_reads - baseline.in_flight_snapshot_reads,
    )
    lock_busy = flow_lock_busy + legacy_lock_busy
    scheduler_skips = max(0.0, current.scheduler_skips - baseline.scheduler_skips)
    work_claim_attempts = max(0.0, current.work_claim_attempts - baseline.work_claim_attempts)
    scheduler_decisions = max(0.0, current.scheduler_decisions - baseline.scheduler_decisions)
    reclaims = max(0.0, current.reclaims - baseline.reclaims)
    duplicate_completion_rejections = max(
        0.0,
        current.duplicate_completion_rejections - baseline.duplicate_completion_rejections,
    )
    decision_latency_buckets = _subtract_metric_buckets(
        current.decision_latency_buckets,
        baseline.decision_latency_buckets,
    )
    candidate_snapshot_latency_buckets = _subtract_metric_buckets(
        current.candidate_snapshot_latency_buckets,
        baseline.candidate_snapshot_latency_buckets,
    )
    in_flight_snapshot_latency_buckets = _subtract_metric_buckets(
        current.in_flight_snapshot_latency_buckets,
        baseline.in_flight_snapshot_latency_buckets,
    )
    lock_busy_denominator = work_claim_attempts or scheduler_decisions
    return {
        "scheduler_decision_p95_seconds": _histogram_quantile_from_buckets(
            decision_latency_buckets,
            0.95,
        ),
        "scheduler_decision_p99_seconds": _histogram_quantile_from_buckets(
            decision_latency_buckets,
            0.99,
        ),
        "scheduler_lock_busy_count": lock_busy,
        "scheduler_flow_lock_busy_count": flow_lock_busy,
        "scheduler_legacy_lock_busy_count": legacy_lock_busy,
        "scheduler_candidate_snapshot_read_count": candidate_snapshot_reads,
        "scheduler_in_flight_snapshot_read_count": in_flight_snapshot_reads,
        "scheduler_candidate_snapshot_p95_seconds": _histogram_quantile_from_buckets(
            candidate_snapshot_latency_buckets,
            0.95,
        ),
        "scheduler_in_flight_snapshot_p95_seconds": _histogram_quantile_from_buckets(
            in_flight_snapshot_latency_buckets,
            0.95,
        ),
        "scheduler_flow_skip_count": scheduler_skips,
        "scheduler_work_claim_attempt_count": work_claim_attempts,
        "scheduler_decision_count": scheduler_decisions,
        "scheduler_lock_busy_rate": lock_busy / max(lock_busy_denominator, 1.0),
        "scheduler_lock_busy_share_of_flow_skips": lock_busy / max(scheduler_skips, 1.0),
        "item_reclaim_rate": reclaims / max(1, int(processed_items)),
        "fairness_deviation_max": _metric_gauge_max(
            "deltallm_batch_scheduler_fairness_deviation",
        ),
        "duplicate_completion_rejections": duplicate_completion_rejections,
    }


def _worker_config_for_scheduler_mode(
    args: argparse.Namespace,
    *,
    worker_index: int = 0,
) -> BatchWorkerConfig:
    scheduler_mode = str(args.scheduler_mode)
    work_slice_mode = scheduler_mode != "fifo_v1"
    worker_count = max(1, int(getattr(args, "workers", 1) or 1))
    return BatchWorkerConfig(
        worker_id="load-worker" if worker_count == 1 else f"load-worker-{worker_index + 1}",
        worker_concurrency=args.worker_concurrency,
        item_claim_limit=args.item_claim_limit,
        item_buffer_multiplier=1,
        scheduler_mode=scheduler_mode,
        scheduler_claim_mode="work_slice" if work_slice_mode else "job_fifo",
        work_claim_max_items=max(1, int(args.item_claim_limit)),
        work_claim_max_work_units=max(1, int(args.item_claim_limit) * 4),
        model_capacity_enabled=scheduler_mode
        in {"model_capacity_v1", "fair_share_v1", "smart_v1"},
        tenant_fair_share_enabled=scheduler_mode in {"fair_share_v1", "smart_v1"},
        size_aware_scheduling_enabled=scheduler_mode == "smart_v1",
        finalization_first=False,
    )


def _model_capacity_resolver_for_scheduler_mode(args: argparse.Namespace) -> Any | None:
    scheduler_mode = str(args.scheduler_mode)
    if scheduler_mode not in {"model_capacity_v1", "fair_share_v1", "smart_v1"}:
        return None
    return StaticModelCapacityResolver(
        model_group=str(args.model),
        service_tier=str(args.service_tier or "standard"),
        max_in_flight_items=max(1, int(args.worker_concurrency) * int(args.item_claim_limit)),
        max_work_units=max(1, int(args.worker_concurrency) * int(args.item_claim_limit) * 4),
    )


async def run_measurement(args: argparse.Namespace) -> BatchLoadMeasurement:
    if not args.database_url:
        raise RuntimeError("DATABASE_URL is required")
    validate_database_url(args.database_url, force=args.force)
    service_tier = str(getattr(args, "service_tier", "standard") or "standard").strip() or "standard"
    if service_tier != "standard":
        raise RuntimeError("--service-tier currently supports only standard batch jobs")

    db = Prisma(datasource={"url": args.database_url})
    await db.connect()
    run_api_key = f"load-script-{uuid4().hex}"
    schema_ready = False
    worker_dbs: list[Prisma] = []
    original_batch_model_allowed = batch_request_validation_module.ensure_batch_model_allowed
    import src.batch.worker as batch_worker_module

    original_execute_embedding = batch_worker_module._execute_embedding
    try:
        batch_request_validation_module.ensure_batch_model_allowed = lambda *args, **kwargs: None
        await ensure_batch_schema(db)
        schema_ready = True

        async def _fake_execute_embedding(request, payload, deployment):  # noqa: ANN001
            del request, payload, deployment
            return {
                "object": "list",
                "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            }

        batch_worker_module._execute_embedding = _fake_execute_embedding
        with tempfile.TemporaryDirectory(prefix="batch-load-") as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "input.jsonl"
            input_file_bytes = write_input_file(
                input_path,
                items=args.items,
                model=args.model,
                input_text=args.input_text,
            )

            repository = BatchRepository(db)
            storage = LocalBatchArtifactStorage(str(temp_path / "artifacts"))
            service = BatchService(repository=repository, storage=storage)
            auth = UserAPIKeyAuth(api_key=run_api_key, models=[args.model])

            upload = FileUpload(input_path)
            start_total = perf_counter()
            upload_start = perf_counter()
            created_file = await service.create_file(auth=auth, upload=upload, purpose="batch")
            await upload.close()
            upload_latency = perf_counter() - upload_start

            create_start = perf_counter()
            created_batch = await service.create_embeddings_batch(
                auth=auth,
                input_file_id=str(created_file["id"]),
                endpoint="/v1/embeddings",
                metadata={"source": "phase3-load-script"},
                completion_window=None,
            )
            create_latency = perf_counter() - create_start
            summary_after_create = await repository.summarize_runtime_statuses(now=datetime.now(tz=UTC))

            scheduler_metrics_before_processing = scheduler_load_metric_snapshot()
            workers: list[BatchExecutorWorker] = []
            for worker_index in range(max(1, int(getattr(args, "workers", 1) or 1))):
                if worker_index == 0:
                    worker_repository = repository
                else:
                    worker_db = Prisma(datasource={"url": args.database_url})
                    await worker_db.connect()
                    worker_dbs.append(worker_db)
                    worker_repository = BatchRepository(worker_db)
                workers.append(
                    BatchExecutorWorker(
                        app=build_worker_app(model=args.model),
                        repository=worker_repository,
                        storage=storage,
                        config=_worker_config_for_scheduler_mode(args, worker_index=worker_index),
                        model_capacity_resolver=_model_capacity_resolver_for_scheduler_mode(args),
                    )
                )
            processing_start = perf_counter()
            if len(workers) == 1:
                await drain_worker(workers[0])
            else:
                await drain_workers(workers, max_rounds=max(100, int(args.items) * 4))
            processing_latency = perf_counter() - processing_start
            total_latency = perf_counter() - start_total

            job = await repository.get_job(str(created_batch["id"]))
            if job is None:
                raise RuntimeError("Created batch disappeared during measurement")
            summary_after_processing = await repository.summarize_runtime_statuses(now=datetime.now(tz=UTC))
            if job.status not in {"completed", "failed", "cancelled"}:
                raise RuntimeError(f"Batch did not reach a terminal state: {job.status}")

            return BatchLoadMeasurement(
                items=args.items,
                workers=max(1, int(getattr(args, "workers", 1) or 1)),
                worker_concurrency=args.worker_concurrency,
                item_claim_limit=args.item_claim_limit,
                input_file_bytes=input_file_bytes,
                upload_latency_seconds=upload_latency,
                batch_create_latency_seconds=create_latency,
                processing_latency_seconds=processing_latency,
                total_latency_seconds=total_latency,
                throughput_items_per_second=float(args.items) / max(processing_latency, 1e-9),
                peak_rss_mib=peak_rss_mib(),
                job_status=job.status,
                runtime_summary_after_create=summary_after_create,
                runtime_summary_after_processing=summary_after_processing,
                request_counts=request_counts(job),
                scheduler_report=scheduler_load_report(
                    processed_items=args.items,
                    baseline=scheduler_metrics_before_processing,
                ),
            )
    finally:
        active_error = sys.exc_info()[1]
        batch_request_validation_module.ensure_batch_model_allowed = original_batch_model_allowed
        batch_worker_module._execute_embedding = original_execute_embedding
        if worker_dbs:
            await asyncio.gather(*(worker_db.disconnect() for worker_db in worker_dbs), return_exceptions=True)
        cleanup_error: Exception | None = None
        if schema_ready:
            try:
                await cleanup_run_data(db, created_by_api_key=run_api_key)
            except Exception as exc:  # pragma: no cover - cleanup must not hide primary failure
                cleanup_error = exc
                if active_error is not None:
                    print(f"warning: batch load cleanup failed: {exc}", file=sys.stderr)
        await db.disconnect()
        if cleanup_error is not None and active_error is None:
            raise cleanup_error


def main() -> int:
    args = build_parser().parse_args()
    measurement = asyncio.run(run_measurement(args))
    print(json.dumps(asdict(measurement), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
