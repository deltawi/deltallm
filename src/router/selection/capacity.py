from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import logging
import math
import secrets
from typing import Protocol

from src.models.errors import ServiceUnavailableError
from src.metrics.selector import observe_selector_cleanup_failure
from src.router.candidates import AttemptCapacity, AttemptCapacityLimit, AttemptPermit
from src.router.health_state import DeploymentHealthRef
from src.router.selection.contracts import (
    SelectorCause,
    SelectorHopFailure,
    SelectorHopOutcome,
    SelectorInvariantError,
    SelectorModelHop,
    SelectorPrompt,
    UnattemptedSelectorUsage,
)

logger = logging.getLogger(__name__)
CAPACITY_CLEANUP_SECONDS = 0.05


class SelectorCapacityOwner(Protocol):
    async def acquire_attempt(
        self, health_ref: DeploymentHealthRef, capacity: AttemptCapacity, *, lease_ttl_seconds: int
    ) -> AttemptPermit: ...

    async def release_attempt(self, permit: AttemptPermit) -> int | None: ...


@dataclass(frozen=True, slots=True)
class SelectorCapacityBounds:
    health_ref: DeploymentHealthRef
    rpm: int
    tpm: int
    concurrency: int
    token_allowance: int

    def __post_init__(self) -> None:
        for value in (self.rpm, self.tpm, self.concurrency, self.token_allowance):
            if type(value) is not int or not 1 <= value <= 2**31 - 1:
                raise SelectorInvariantError()


class CapacityAdmittedSelectorHop:
    """Uses canonical router admission, with no local fallback or caller RPM charge.

    RPM and the conservative TPM bound are consumed atomically. They are not refunded
    for unknown dispatch outcomes; the minute window expires normally. Actual usage
    belongs to durable billing and must not increment these counters a second time.
    """

    def __init__(
        self, *, owner: SelectorCapacityOwner, hop: SelectorModelHop, bounds: SelectorCapacityBounds
    ) -> None:
        self._owner, self._hop, self._bounds = owner, hop, bounds

    async def invoke(
        self, *, deployment_id: str, prompt: SelectorPrompt, expires_at: float
    ) -> SelectorHopOutcome:
        bounds = self._bounds
        if deployment_id != bounds.health_ref.deployment_id or not math.isfinite(expires_at):
            raise SelectorInvariantError()
        remaining = expires_at - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError()
        owner_token = secrets.token_urlsafe(24)
        capacity = AttemptCapacity(
            limits=(
                AttemptCapacityLimit("rpm", bounds.rpm, 1),
                AttemptCapacityLimit("tpm", bounds.tpm, bounds.token_allowance),
            ),
            max_concurrency=bounds.concurrency,
            require_shared=True,
            owner_token=owner_token,
        )
        # The token is known before the await: cleanup can release an ambiguous acquire.
        permit = AttemptPermit(deployment_id, bounds.health_ref, True, "redis", owner_token)
        try:
            async with asyncio.timeout_at(expires_at):
                try:
                    acquired = await self._owner.acquire_attempt(
                        bounds.health_ref,
                        capacity,
                        lease_ttl_seconds=max(1, math.ceil(remaining + CAPACITY_CLEANUP_SECONDS)),
                    )
                except ServiceUnavailableError:
                    return _denied(SelectorCause.CAPACITY_UNAVAILABLE)
                if not acquired.acquired:
                    permit = acquired
                    return _denied(SelectorCause.CAPACITY_DENIED)
                if acquired.backend != "redis" or acquired.owner_token != owner_token:
                    raise SelectorInvariantError()
                permit = acquired
                return await self._hop.invoke(
                    deployment_id=deployment_id, prompt=prompt, expires_at=expires_at
                )
        finally:
            if permit.acquired:
                await self._release(permit)

    async def _release(self, permit: AttemptPermit) -> None:
        try:
            async with asyncio.timeout(CAPACITY_CLEANUP_SECONDS):
                released = await self._owner.release_attempt(replace(permit, acquired=True))
                if released is None:
                    observe_selector_cleanup_failure()
                    logger.warning("selector_capacity_cleanup_incomplete")
        except Exception:
            # TTL is the bounded recovery path; never expose backend text or affect health.
            observe_selector_cleanup_failure()
            logger.warning("selector_capacity_cleanup_incomplete")


def _denied(cause: SelectorCause) -> SelectorHopFailure:
    return SelectorHopFailure(cause=cause, usage=UnattemptedSelectorUsage())
