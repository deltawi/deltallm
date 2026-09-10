from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from src.batch.chat_batching import resolve_chat_batching_settings
from src.models.errors import GatewayCapacityError
from src.router.attempt_capacity import AttemptSlotAdmission, bind_attempt_capacity
from src.router.execution import RequestDeadline
from src.router.router import Deployment, Router
from src.batch.worker_types import _PreparedChatItem


class ChatDeploymentCapacity(AttemptSlotAdmission):
    """Slice-owned answer slots, shared by single and microbatch attempts.

    Captured generations register before execution, so an unchanged deployment
    has one allowance even if its pinned settings disagree. No provider slot is
    held across fallback, split-to-single work, or paid classification.
    """

    def __init__(self, worker_concurrency: int) -> None:
        self._maximum = max(1, worker_concurrency)
        self._limits: dict[str, int] = {}
        self._active: dict[str, int] = {}
        self._total = 0
        self._changed = asyncio.Condition()

    def register(self, deployment: Deployment) -> None:
        configured = resolve_chat_batching_settings(deployment.deltallm_params).max_in_flight
        limit = max(1, min(configured or self._maximum, self._maximum))
        key = deployment.deployment_id
        self._limits[key] = min(limit, self._limits.get(key, limit))

    @asynccontextmanager
    async def slot(self, deployment: Deployment, deadline: RequestDeadline) -> AsyncIterator[None]:
        self.register(deployment)
        key = deployment.deployment_id
        try:
            if deadline.remaining() <= 0:
                raise TimeoutError()
            async with asyncio.timeout_at(deadline.expires_at):
                async with self._changed:
                    await self._changed.wait_for(
                        lambda: (
                            self._total < self._maximum
                            and self._active.get(key, 0) < self._limits[key]
                        )
                    )
                    self._active[key] = self._active.get(key, 0) + 1
                    self._total += 1
        except TimeoutError:
            raise GatewayCapacityError(
                message="Batch execution capacity unavailable",
                code="batch_execution_capacity_unavailable",
                affects_deployment_health=False,
            ) from None
        try:
            yield
        finally:
            async with self._changed:
                self._active[key] -= 1
                self._total -= 1
                self._changed.notify_all()


def bind_chat_capacity(
    items: Iterable[_PreparedChatItem], *, worker_concurrency: int
) -> ChatDeploymentCapacity:
    capacity = ChatDeploymentCapacity(worker_concurrency)
    generations: set[str] = set()
    for prepared in items:
        runtime = prepared.routing_generation
        if runtime.generation_id not in generations and isinstance(runtime.router, Router):
            generations.add(runtime.generation_id)
            for deployments in runtime.router.deployment_registry.values():
                for deployment in deployments:
                    capacity.register(deployment)
        if prepared.primary_deployment is not None:
            capacity.register(prepared.primary_deployment)
        bind_attempt_capacity(prepared.request_context, capacity)
    return capacity
