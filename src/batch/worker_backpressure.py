from __future__ import annotations

import logging

from src.batch.backpressure import BatchModelGroupDeferred
from src.batch.models import BatchItemRecord
from src.batch.retry import BatchRetryCategory, BatchRetryDecision
from src.metrics import (
    increment_batch_model_group_deferral,
    increment_batch_model_group_deferred_items,
    observe_batch_model_group_deferral_seconds,
)

logger = logging.getLogger(__name__)


class WorkerBackpressureMixin:
    def _batch_backpressure_coordinator(self):  # noqa: ANN201
        coordinator = getattr(self.app.state, "batch_backpressure", None)
        if coordinator is None or not getattr(coordinator, "enabled", True):
            return None
        return coordinator

    def _resolve_item_model_group(
        self, *, item: BatchItemRecord | None, model_name: str | None
    ) -> str | None:
        app_router = getattr(self.app.state, "router", None)
        if app_router is None:
            return None
        model = str(model_name or "").strip()
        if not model and item is not None:
            request_body = item.request_body if isinstance(item.request_body, dict) else {}
            model = str(request_body.get("model") or "").strip()
        if not model:
            return None
        try:
            return str(app_router.resolve_model_group(model))
        except Exception as exc:
            logger.debug(
                "batch model-group backpressure model resolution skipped model=%s error=%s",
                model,
                exc,
            )
            return None

    async def _get_model_group_deferral(self, model_group: str):  # noqa: ANN201
        coordinator = self._batch_backpressure_coordinator()
        if coordinator is None:
            return None
        try:
            return await coordinator.get_model_group_deferral(model_group)
        except Exception as exc:
            logger.warning(
                "batch model-group backpressure read failed model_group=%s error=%s",
                model_group,
                exc,
                exc_info=True,
            )
            return None

    async def _raise_if_model_group_deferred(self, model_group: str) -> None:
        deferral = await self._get_model_group_deferral(model_group)
        if deferral is None:
            return
        retry_after = max(1, int(deferral.remaining_seconds()))
        logger.info(
            "batch item deferred by model group backpressure model_group=%s reason=%s delay_seconds=%s",
            model_group,
            deferral.reason,
            retry_after,
        )
        raise BatchModelGroupDeferred(
            model_group=model_group,
            reason=deferral.reason,
            retry_after_seconds=retry_after,
        )

    def _record_model_group_deferral(self, *, reason: str, delay_seconds: int) -> None:
        try:
            increment_batch_model_group_deferral(reason=reason)
            observe_batch_model_group_deferral_seconds(reason=reason, delay_seconds=delay_seconds)
        except Exception as exc:
            logger.warning(
                "batch model-group backpressure metric publish failed reason=%s error=%s",
                reason,
                exc,
            )

    def _record_model_group_deferred_item(self, *, reason: str) -> None:
        try:
            increment_batch_model_group_deferred_items(reason=reason)
        except Exception as exc:
            logger.warning(
                "batch model-group deferred item metric publish failed reason=%s error=%s",
                reason,
                exc,
            )

    async def _maybe_defer_model_group_for_retry(
        self,
        *,
        item: BatchItemRecord | None,
        model_name: str | None,
        model_group: str | None,
        exc: Exception,
        decision: BatchRetryDecision,
        retry_delay_seconds: int,
    ) -> None:
        if decision.category is not BatchRetryCategory.NO_HEALTHY_DEPLOYMENTS:
            return
        if isinstance(exc, BatchModelGroupDeferred):
            return

        resolved_model_group = model_group or self._resolve_item_model_group(
            item=item, model_name=model_name
        )
        if not resolved_model_group:
            return

        coordinator = self._batch_backpressure_coordinator()
        if coordinator is None:
            return

        reason = decision.category.value
        try:
            deferral = await coordinator.defer_model_group(
                resolved_model_group,
                delay_seconds=retry_delay_seconds,
                reason=reason,
            )
        except Exception as defer_exc:
            logger.warning(
                "batch model-group backpressure deferral failed model_group=%s reason=%s error=%s",
                resolved_model_group,
                reason,
                defer_exc,
                exc_info=True,
            )
            return
        if deferral is None:
            return

        effective_delay_seconds = max(0, int(deferral.remaining_seconds()))
        effective_reason = str(deferral.reason or reason)
        self._record_model_group_deferral(
            reason=effective_reason, delay_seconds=effective_delay_seconds
        )
        logger.info(
            "batch model group deferred model_group=%s reason=%s delay_seconds=%s",
            deferral.model_group,
            effective_reason,
            effective_delay_seconds,
        )
