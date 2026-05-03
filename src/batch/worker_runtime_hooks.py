from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

from src.batch.endpoints import batch_call_type_for_endpoint

logger = logging.getLogger(__name__)


class WorkerRuntimeHooksMixin:
    async def _record_upstream_success_runtime_hooks(
        self,
        *,
        batch_id: str,
        deployment_id: str,
        mode: str,
        usage: dict[str, Any],
        reference: str,
    ) -> None:
        passive_health_tracker = getattr(self.app.state, "passive_health_tracker", None)
        if passive_health_tracker is not None and deployment_id:
            try:
                await passive_health_tracker.record_request_outcome(deployment_id, success=True)
            except Exception as exc:
                logger.warning(
                    "batch passive health success hook failed batch_id=%s reference=%s deployment_id=%s error=%s",
                    batch_id,
                    reference,
                    deployment_id,
                    exc,
                )
        router_state_backend = getattr(self.app.state, "router_state_backend", None)
        if router_state_backend is not None and deployment_id:
            try:
                await self._record_router_usage(
                    router_state_backend,
                    deployment_id,
                    mode=mode,
                    usage=usage,
                )
            except Exception as exc:
                logger.warning(
                    "batch router usage hook failed batch_id=%s reference=%s deployment_id=%s error=%s",
                    batch_id,
                    reference,
                    deployment_id,
                    exc,
                )

    async def _record_upstream_failure_runtime_hook(
        self,
        *,
        batch_id: str,
        deployment_id: str | None,
        exc: Exception,
        reference: str,
    ) -> None:
        passive_health_tracker = getattr(self.app.state, "passive_health_tracker", None)
        if passive_health_tracker is None or deployment_id is None:
            return
        try:
            await passive_health_tracker.record_request_outcome(
                deployment_id,
                success=False,
                error=str(exc),
                exc=exc,
            )
        except Exception as hook_exc:
            logger.warning(
                "batch passive health upstream failure hook failed batch_id=%s reference=%s deployment_id=%s error=%s",
                batch_id,
                reference,
                deployment_id,
                hook_exc,
            )

    async def _record_failure_runtime_hooks(
        self,
        *,
        job,
        item,
        batch_id: str,
        model_name: str,
        exc: Exception,
        deployment_id: str | None,
    ) -> None:
        passive_health_tracker = getattr(self.app.state, "passive_health_tracker", None)
        if passive_health_tracker is not None and deployment_id is not None:
            try:
                await passive_health_tracker.record_request_outcome(
                    deployment_id,
                    success=False,
                    error=str(exc),
                    exc=exc,
                )
            except Exception as hook_exc:
                logger.warning(
                    "batch passive health failure hook failed batch_id=%s item_id=%s deployment_id=%s error=%s",
                    batch_id,
                    item.item_id,
                    deployment_id,
                    hook_exc,
                )
        spend_tracking_service = getattr(self.app.state, "spend_tracking_service", None)
        if job.created_by_api_key and spend_tracking_service is not None:
            try:
                await spend_tracking_service.log_request_failure(
                    request_id=f"batch:{batch_id}:{item.item_id}",
                    api_key=job.created_by_api_key,
                    user_id=job.created_by_user_id,
                    team_id=job.created_by_team_id,
                    organization_id=job.created_by_organization_id,
                    end_user_id=None,
                    model=model_name,
                    call_type=batch_call_type_for_endpoint(job.endpoint),
                    metadata={
                        "batch_id": batch_id,
                        "batch_item_id": item.item_id,
                        "custom_id": item.custom_id,
                        "endpoint": job.endpoint,
                    },
                    cache_hit=False,
                    start_time=None,
                    end_time=datetime.now(tz=UTC),
                    exc=exc,
                )
            except Exception as hook_exc:
                logger.warning(
                    "batch failure logging hook failed batch_id=%s item_id=%s error=%s",
                    batch_id,
                    item.item_id,
                    hook_exc,
                )
