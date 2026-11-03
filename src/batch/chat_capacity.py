from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from src.batch.chat_batching import resolve_chat_batching_settings
from src.models.errors import GatewayCapacityError
from src.router.attempt_capacity import AttemptSlotAdmission, bind_attempt_capacity
from src.router.execution import RequestDeadline
from src.router.router import Deployment, Router
from src.batch.worker_types import _PreparedChatItem


class ChatAttemptCounts:
    """Pod-wide answer slots in use, shared by every slice a worker runs at once.

    Each open slice registers its deployment allowances here, and every slice
    checks the lowest one still registered. So two slices on one deployment
    never exceed its ``max_in_flight`` together, even while one of them was
    captured before a config change lowered it.
    """

    def __init__(self) -> None:
        self.active: dict[str, int] = {}
        self.total = 0
        self.changed = asyncio.Condition()
        self._limits: dict[str, Counter[int]] = {}

    def limit(self, key: str, own: int) -> int:
        shared = self._limits.get(key)
        return min(own, min(shared)) if shared else own

    def hold(self, key: str, limit: int) -> None:
        self._limits.setdefault(key, Counter())[limit] += 1

    def drop(self, key: str, limit: int) -> None:
        shared = self._limits.get(key)
        if not shared:
            return
        shared[limit] -= 1
        if shared[limit] <= 0:
            del shared[limit]
        if not shared:
            del self._limits[key]


class ChatDeploymentCapacity(AttemptSlotAdmission):
    """Slice-owned answer allowances, shared by single and microbatch attempts.

    Captured generations register before execution, so an unchanged deployment
    has one allowance even if its pinned settings disagree. No provider slot is
    held across fallback, split-to-single work, or paid classification.
    """

    def __init__(
        self,
        worker_concurrency: int,
        counts: ChatAttemptCounts | None = None,
        *,
        shared: bool = False,
    ) -> None:
        self._maximum = max(1, worker_concurrency)
        self._limits: dict[str, int] = {}
        self._counts = counts if counts is not None else ChatAttemptCounts()
        # A shared capacity registers its allowances pod-wide until close().
        self._shared = shared and counts is not None

    @property
    def _total(self) -> int:
        return self._counts.total

    def register(self, deployment: Deployment) -> None:
        configured = resolve_chat_batching_settings(deployment.deltallm_params).max_in_flight
        limit = max(1, min(configured or self._maximum, self._maximum))
        key = deployment.deployment_id
        previous = self._limits.get(key)
        current = min(limit, previous if previous is not None else limit)
        if current == previous:
            return
        self._limits[key] = current
        if self._shared:
            if previous is not None:
                self._counts.drop(key, previous)
            self._counts.hold(key, current)

    async def close(self) -> None:
        """Stop counting this slice's allowances; a raised limit wakes waiters."""
        if not self._shared:
            return
        self._shared = False
        for key, limit in self._limits.items():
            self._counts.drop(key, limit)
        async with self._counts.changed:
            self._counts.changed.notify_all()

    @asynccontextmanager
    async def slot(self, deployment: Deployment, deadline: RequestDeadline) -> AsyncIterator[None]:
        self.register(deployment)
        key = deployment.deployment_id
        counts = self._counts
        try:
            if deadline.remaining() <= 0:
                raise TimeoutError()
            async with asyncio.timeout_at(deadline.expires_at):
                async with counts.changed:
                    await counts.changed.wait_for(
                        lambda: (
                            counts.total < self._maximum
                            and counts.active.get(key, 0) < counts.limit(key, self._limits[key])
                        )
                    )
                    counts.active[key] = counts.active.get(key, 0) + 1
                    counts.total += 1
        except TimeoutError:
            raise GatewayCapacityError(
                message="Batch execution capacity unavailable",
                code="batch_execution_capacity_unavailable",
                affects_deployment_health=False,
            ) from None
        try:
            yield
        finally:
            async with counts.changed:
                counts.active[key] -= 1
                if counts.active[key] <= 0:
                    del counts.active[key]
                counts.total -= 1
                counts.changed.notify_all()


def bind_chat_capacity(
    items: Iterable[_PreparedChatItem],
    *,
    worker_concurrency: int,
    counts: ChatAttemptCounts | None = None,
) -> ChatDeploymentCapacity:
    """Bind one slice's capacity; with ``counts`` it is pod-wide until close()."""
    capacity = ChatDeploymentCapacity(worker_concurrency, counts, shared=counts is not None)
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
