from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

from src.batch.endpoints import batch_call_type_for_endpoint
from src.batch.policy import acquire_batch_policy_lease, release_batch_policy_lease
from src.batch.worker_types import _PreparedChatItem, _PreparedEmbeddingItem
from src.billing.cost import ModelPricing

logger = logging.getLogger(__name__)


class WorkerPersistenceMixin:
    def _deployment_pricing(self, deployment) -> ModelPricing | None:  # noqa: ANN001
        if deployment.input_cost_per_token or deployment.output_cost_per_token:
            return ModelPricing(
                input_cost_per_token=deployment.input_cost_per_token,
                output_cost_per_token=deployment.output_cost_per_token,
            )
        return None

    def _observe_item_execution_latency(
        self, *, status: str, latency_seconds: float, reference: str
    ) -> None:
        try:
            self._observe_batch_item_execution_latency(
                status=status, latency_seconds=latency_seconds
            )
        except Exception as exc:
            logger.warning(
                "batch item latency metric publish failed reference=%s status=%s error=%s",
                reference,
                status,
                exc,
            )

    async def _acquire_prepared_policy_lease(self, *, prepared: _PreparedEmbeddingItem | _PreparedChatItem) -> None:
        if prepared.policy_lease is not None:
            return
        if prepared.policy_auth is None:
            return
        prepared.policy_lease = await acquire_batch_policy_lease(
            app=self.app,
            payload=prepared.payload,
            auth=prepared.policy_auth,
        )

    async def _release_prepared_policy_lease(self, prepared: _PreparedEmbeddingItem | _PreparedChatItem) -> None:
        lease = prepared.policy_lease
        prepared.policy_lease = None
        await release_batch_policy_lease(app=self.app, lease=lease)

    async def _release_prepared_policy_leases(self, prepared_items: list[_PreparedEmbeddingItem] | list[_PreparedChatItem]) -> None:
        for prepared in prepared_items:
            await self._release_prepared_policy_lease(prepared)

    async def _renew_item_lease_once(self, item_id: str) -> bool:
        try:
            return await self.repository.renew_item_lease(
                item_id=item_id,
                worker_id=self.config.worker_id,
                lease_seconds=self.config.item_lease_seconds,
            )
        except Exception as exc:
            logger.warning(
                "batch item lease renewal before persistence failed item_id=%s error=%s",
                item_id,
                exc,
                exc_info=True,
            )
            return False

    def _build_completion_outbox_payload(
        self,
        *,
        job,
        prepared: _PreparedEmbeddingItem | _PreparedChatItem,
        usage: dict[str, Any],
        api_provider: str,
        billed_cost: float,
        provider_cost: float,
        api_base: str | None,
        deployment_model: str | None,
        batch_execution_mode: str | None = None,
        microbatch_size: int | None = None,
        microbatch_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "request_id": f"batch:{job.batch_id}:{prepared.item.item_id}",
            "batch_id": job.batch_id,
            "item_id": prepared.item.item_id,
            "api_key": job.created_by_api_key,
            "user_id": job.created_by_user_id,
            "team_id": job.created_by_team_id,
            "organization_id": job.created_by_organization_id,
            "model": prepared.payload.model,
            "call_type": batch_call_type_for_endpoint(job.endpoint),
            "usage": dict(usage),
            "billed_cost": billed_cost,
            "provider_cost": provider_cost,
            "api_provider": api_provider,
            "api_base": api_base,
            "deployment_model": deployment_model,
            "execution_mode": job.execution_mode,
            "completed_at": datetime.now(tz=UTC).isoformat(),
        }
        if batch_execution_mode is not None:
            payload["batch_execution_mode"] = batch_execution_mode
        if microbatch_size is not None:
            payload["microbatch_size"] = int(microbatch_size)
        if microbatch_id is not None:
            payload["microbatch_id"] = microbatch_id
        return payload

    async def _persist_completion_rows_with_outbox(
        self,
        *,
        items: list[dict[str, Any]],
        item_ids: list[str],
        context_label: str,
    ) -> bool:
        for attempt in range(2):
            try:
                result = await self.repository.complete_items_with_outbox_bulk(
                    items=items,
                    worker_id=self.config.worker_id,
                )
            except Exception as exc:
                logger.warning(
                    "batch completion persistence attempt failed context=%s item_ids=%s attempt=%s error=%s",
                    context_label,
                    item_ids,
                    attempt + 1,
                    exc,
                    exc_info=True,
                )
                continue

            if result in {"completed", "already_completed"}:
                return True
            if result == "not_owned":
                logger.warning(
                    "batch completion persistence lost ownership context=%s item_ids=%s",
                    context_label,
                    item_ids,
                )
                return False

        try:
            requeued_item_ids = await self.repository.release_items_for_retry(
                item_ids=item_ids,
                worker_id=self.config.worker_id,
            )
        except Exception as exc:
            logger.warning(
                "batch completion persistence requeue failed context=%s item_ids=%s error=%s",
                context_label,
                item_ids,
                exc,
                exc_info=True,
            )
            return False

        expected_ids = set(item_ids)
        if set(requeued_item_ids) != expected_ids:
            logger.warning(
                "batch completion persistence requeue incomplete context=%s item_ids=%s requeued_item_ids=%s",
                context_label,
                item_ids,
                requeued_item_ids,
            )
            return False
        logger.warning(
            "batch completion persistence requeued context=%s item_ids=%s",
            context_label,
            item_ids,
        )
        return False
