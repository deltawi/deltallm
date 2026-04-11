from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.batch.models import BatchCompletionOutboxRecord
from src.batch.repository import BatchRepository
from src.billing.spend import SpendTrackingService
from src.metrics import increment_request, increment_spend, increment_usage

logger = logging.getLogger(__name__)


@dataclass
class BatchCompletionOutboxWorkerConfig:
    worker_id: str = "batch-completion-outbox"
    poll_interval_seconds: float = 1.0
    max_batch_size: int = 50
    max_concurrency: int = 4
    lease_seconds: int = 30
    heartbeat_interval_seconds: float = 10.0
    retry_initial_seconds: int = 5
    retry_max_seconds: int = 300


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return None


class BatchCompletionOutboxWorker:
    def __init__(
        self,
        *,
        app: Any,
        repository: BatchRepository,
        config: BatchCompletionOutboxWorkerConfig | None = None,
    ) -> None:
        self.app = app
        self.repository = repository
        self.config = config or BatchCompletionOutboxWorkerConfig()
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        while not self._stopped:
            processed = await self.process_once()
            if processed == 0:
                await asyncio.sleep(self.config.poll_interval_seconds)

    async def process_once(self) -> int:
        claimed = await self.repository.claim_completion_outbox_due(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            limit=self.config.max_batch_size,
        )
        if not claimed:
            return 0

        semaphore = asyncio.Semaphore(max(1, min(self.config.max_concurrency, len(claimed))))

        async def _run(record: BatchCompletionOutboxRecord) -> None:
            async with semaphore:
                await self._process_record(record)

        await asyncio.gather(*[_run(record) for record in claimed])
        return len(claimed)

    async def _process_record(self, record: BatchCompletionOutboxRecord) -> None:
        payload = dict(record.payload_json or {})
        heartbeat_task = self._start_heartbeat(record.completion_id)
        try:
            delivered = await self._record_durable_success(record, payload)
        except Exception as exc:
            logger.warning(
                "batch completion outbox delivery failed completion_id=%s item_id=%s error=%s",
                record.completion_id,
                record.item_id,
                exc,
                exc_info=True,
            )
            if record.attempt_count >= record.max_attempts:
                updated = await self.repository.mark_completion_outbox_failed(
                    record.completion_id,
                    worker_id=self.config.worker_id,
                    error=str(exc),
                )
                if not updated:
                    logger.info(
                        "batch completion outbox failed finalize skipped after lease loss completion_id=%s",
                        record.completion_id,
                    )
                return
            retry_seconds = min(
                self.config.retry_max_seconds,
                max(self.config.retry_initial_seconds, self.config.retry_initial_seconds * max(1, record.attempt_count)),
            )
            updated = await self.repository.mark_completion_outbox_retry(
                record.completion_id,
                worker_id=self.config.worker_id,
                error=str(exc),
                next_attempt_at=datetime.now(tz=UTC) + timedelta(seconds=retry_seconds),
            )
            if not updated:
                logger.info(
                    "batch completion outbox retry finalize skipped after lease loss completion_id=%s",
                    record.completion_id,
                )
            return
        finally:
            await self._stop_heartbeat(heartbeat_task)

        if delivered:
            self._publish_metrics(payload)

    async def _record_durable_success(self, record: BatchCompletionOutboxRecord, payload: dict[str, Any]) -> bool:
        db = getattr(self.repository, "prisma", None)
        spend_tracking_service = getattr(self.app.state, "spend_tracking_service", None)
        if db is not None and hasattr(db, "tx"):
            async with db.tx() as tx:
                tx_repository = self.repository.with_prisma(tx)
                return await self._record_durable_success_with_dependencies(
                    repository=tx_repository,
                    spend_tracking_service=(
                        spend_tracking_service.with_db(tx)
                        if spend_tracking_service is not None and hasattr(spend_tracking_service, "with_db")
                        else SpendTrackingService(tx)
                    ),
                    record=record,
                    payload=payload,
                )

        return await self._record_durable_success_with_dependencies(
            repository=self.repository,
            spend_tracking_service=spend_tracking_service,
            record=record,
            payload=payload,
        )

    async def _record_durable_success_with_dependencies(
        self,
        *,
        repository: BatchRepository,
        spend_tracking_service,
        record: BatchCompletionOutboxRecord,
        payload: dict[str, Any],
    ) -> bool:
        api_key = str(payload.get("api_key") or "").strip()
        if api_key:
            service = spend_tracking_service
            if service is None:
                service = SpendTrackingService(getattr(repository, "prisma", None))
            completed_at = _parse_datetime(payload.get("completed_at")) or datetime.now(tz=UTC)
            outcome = await service.log_spend_once(
                event_id=record.completion_id,
                request_id=str(payload.get("request_id") or f"batch:{record.batch_id}:{record.item_id}"),
                api_key=api_key,
                user_id=str(payload.get("user_id")) if payload.get("user_id") is not None else None,
                team_id=str(payload.get("team_id")) if payload.get("team_id") is not None else None,
                organization_id=(
                    str(payload.get("organization_id")) if payload.get("organization_id") is not None else None
                ),
                end_user_id=None,
                model=str(payload.get("model") or ""),
                call_type=str(payload.get("call_type") or "embedding_batch"),
                usage=dict(payload.get("usage") or {}),
                cost=float(payload.get("billed_cost") or 0.0),
                metadata={
                    "api_base": payload.get("api_base"),
                    "provider": payload.get("api_provider"),
                    "deployment_model": payload.get("deployment_model"),
                    "batch_id": payload.get("batch_id"),
                    "batch_item_id": payload.get("item_id"),
                    "execution_mode": payload.get("execution_mode"),
                    "provider_cost": payload.get("provider_cost"),
                    "pricing_tier": "batch",
                },
                cache_hit=False,
                start_time=completed_at,
                end_time=completed_at,
            )
            if outcome not in {"inserted", "duplicate"}:
                raise RuntimeError(f"unexpected spend log outcome: {outcome}")
        return await repository.mark_completion_outbox_sent(
            record.completion_id,
            worker_id=self.config.worker_id,
        )

    def _start_heartbeat(self, completion_id: str) -> asyncio.Task[None]:
        return asyncio.create_task(self._heartbeat_loop(completion_id))

    async def _stop_heartbeat(self, task: asyncio.Task[None]) -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _heartbeat_loop(self, completion_id: str) -> None:
        interval_seconds = max(0.1, min(self.config.heartbeat_interval_seconds, self.config.lease_seconds / 2))
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                renewed = await self.repository.renew_completion_outbox_lease(
                    completion_id=completion_id,
                    worker_id=self.config.worker_id,
                    lease_seconds=self.config.lease_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "batch completion outbox lease renewal failed completion_id=%s error=%s",
                    completion_id,
                    exc,
                    exc_info=True,
                )
                continue
            if not renewed:
                logger.info("batch completion outbox lease lost completion_id=%s", completion_id)
                return

    def _publish_metrics(self, payload: dict[str, Any]) -> None:
        try:
            increment_request(
                model=str(payload.get("model") or ""),
                api_provider=str(payload.get("api_provider") or "unknown"),
                api_key=str(payload.get("api_key")) if payload.get("api_key") is not None else None,
                user=str(payload.get("user_id")) if payload.get("user_id") is not None else None,
                team=str(payload.get("team_id")) if payload.get("team_id") is not None else None,
                status_code=200,
            )
            usage = dict(payload.get("usage") or {})
            increment_usage(
                model=str(payload.get("model") or ""),
                api_provider=str(payload.get("api_provider") or "unknown"),
                api_key=str(payload.get("api_key")) if payload.get("api_key") is not None else None,
                user=str(payload.get("user_id")) if payload.get("user_id") is not None else None,
                team=str(payload.get("team_id")) if payload.get("team_id") is not None else None,
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            )
            increment_spend(
                model=str(payload.get("model") or ""),
                api_provider=str(payload.get("api_provider") or "unknown"),
                api_key=str(payload.get("api_key")) if payload.get("api_key") is not None else None,
                user=str(payload.get("user_id")) if payload.get("user_id") is not None else None,
                team=str(payload.get("team_id")) if payload.get("team_id") is not None else None,
                spend=float(payload.get("billed_cost") or 0.0),
            )
        except Exception as exc:  # pragma: no cover - metrics must not break delivery
            logger.warning(
                "batch completion outbox metrics publish failed completion_request_id=%s error=%s",
                payload.get("request_id"),
                exc,
            )
