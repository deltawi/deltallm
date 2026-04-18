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

import src.batch.service as batch_service_module
from src.batch.models import BatchJobRecord
from src.batch.repository import BatchRepository
from src.batch.service import BatchService
from src.batch.storage import LocalBatchArtifactStorage
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
    async def record_request_outcome(self, deployment_id: str, success: bool, error: str | None = None) -> None:
        del deployment_id, success, error


class NoopRouterStateBackend:
    async def increment_usage_counters(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs


@dataclass
class BatchLoadMeasurement:
    items: int
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure embedding batch create/process throughput and memory.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="Postgres DATABASE_URL to use")
    parser.add_argument("--items", type=int, default=1000, help="Number of embedding items to generate")
    parser.add_argument("--worker-concurrency", type=int, default=8, help="Batch worker per-process concurrency")
    parser.add_argument("--item-claim-limit", type=int, default=8, help="Batch worker item claim limit")
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
        raise RuntimeError("Batch tables are missing. Run `uv run prisma migrate deploy --schema=./prisma/schema.prisma` first.")


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


def request_counts(job: BatchJobRecord) -> dict[str, int]:
    return {
        "total": job.total_items,
        "completed": job.completed_items,
        "failed": job.failed_items,
        "cancelled": job.cancelled_items,
        "in_progress": job.in_progress_items,
    }


async def run_measurement(args: argparse.Namespace) -> BatchLoadMeasurement:
    if not args.database_url:
        raise RuntimeError("DATABASE_URL is required")
    validate_database_url(args.database_url, force=args.force)

    db = Prisma(datasource={"url": args.database_url})
    await db.connect()
    run_api_key = f"load-script-{uuid4().hex}"
    schema_ready = False
    original_batch_model_allowed = batch_service_module.ensure_batch_model_allowed
    import src.batch.worker as batch_worker_module

    original_execute_embedding = batch_worker_module._execute_embedding
    try:
        batch_service_module.ensure_batch_model_allowed = lambda *args, **kwargs: None
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

            worker = BatchExecutorWorker(
                app=build_worker_app(model=args.model),
                repository=repository,
                storage=storage,
                config=BatchWorkerConfig(
                    worker_id="load-worker",
                    worker_concurrency=args.worker_concurrency,
                    item_claim_limit=args.item_claim_limit,
                    item_buffer_multiplier=1,
                ),
            )
            processing_start = perf_counter()
            await drain_worker(worker)
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
            )
    finally:
        active_error = sys.exc_info()[1]
        batch_service_module.ensure_batch_model_allowed = original_batch_model_allowed
        batch_worker_module._execute_embedding = original_execute_embedding
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
