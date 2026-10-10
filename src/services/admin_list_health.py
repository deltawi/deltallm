from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from collections.abc import Awaitable, Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, TypeVar

from src.router.health_state import HealthRefInput
from src.router.registry import DeploymentRegistryStore

if TYPE_CHECKING:
    from src.router.router import Deployment

logger = logging.getLogger(__name__)
_ReadValue = TypeVar("_ReadValue")


class AdminHealthReader(Protocol):
    async def get_health_batch(
        self, refs: list[HealthRefInput]
    ) -> Mapping[str, Mapping[str, object]]: ...

    async def get_cooldown_batch(self, refs: list[HealthRefInput]) -> Mapping[str, bool]: ...

    def get_backend_status(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class ListHealthSnapshot:
    healthy_ids: list[str]
    unknown_ids: list[str]


def list_health_refs(
    registry: Mapping[str, Sequence[Deployment]] | None, deployment_ids: list[str]
) -> dict[str, HealthRefInput]:
    physical = (
        registry.physical_deployments
        if isinstance(registry, DeploymentRegistryStore)
        else {
            deployment.deployment_id: deployment
            for group in (registry or {}).values()
            for deployment in group
        }
    )
    return {key: physical[key].health_ref if key in physical else key for key in deployment_ids}


async def _read_with_availability(
    backend: AdminHealthReader, read: Awaitable[_ReadValue]
) -> tuple[_ReadValue, bool]:
    value = await read
    # Capture status before another successful read can clear the failure state.
    return value, backend.get_backend_status().get("mode") == "redis"


async def list_health_snapshot(
    backend: AdminHealthReader | None,
    refs_by_id: dict[str, HealthRefInput],
) -> ListHealthSnapshot:
    if not refs_by_id:
        return ListHealthSnapshot([], [])
    if backend is None:
        return ListHealthSnapshot([], list(refs_by_id))
    refs = list(refs_by_id.values())
    try:
        (health, health_available), (cooldown, cooldown_available) = await asyncio.gather(
            _read_with_availability(backend, backend.get_health_batch(refs)),
            _read_with_availability(backend, backend.get_cooldown_batch(refs)),
        )
        if (
            not health_available
            or not cooldown_available
            or backend.get_backend_status().get("mode") != "redis"
        ):
            return ListHealthSnapshot([], list(refs_by_id))
    except Exception as exc:
        logger.warning("Admin list health is unavailable: %s", type(exc).__name__)
        return ListHealthSnapshot([], list(refs_by_id))
    healthy = [
        deployment_id
        for deployment_id in refs_by_id
        if str(health.get(deployment_id, {}).get("healthy", "true")).lower() != "false"
        and not cooldown.get(deployment_id, False)
    ]
    return ListHealthSnapshot(healthy, [])
