from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from src.db.route_policy_lifecycle import RoutePolicyRecord, RoutePolicyWriteResult


class RoutePolicyPublicationRepository(Protocol):
    async def publish_policy(
        self,
        group_key: str,
        policy_json: dict[str, object],
        *,
        published_by: str | None = None,
    ) -> RoutePolicyWriteResult | None: ...

    async def publish_latest_draft(
        self,
        group_key: str,
        *,
        published_by: str | None = None,
    ) -> RoutePolicyRecord | None: ...


RoutePolicyRuntimeRefresh = Callable[[], Awaitable[tuple[str, ...]]]


class RoutePolicyPublicationNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class RoutePolicyPublicationResult:
    policy: RoutePolicyRecord
    warnings: tuple[str, ...] = ()


class RoutePolicyPublicationService:
    """Publish through locked repository validation, then refresh once after commit."""

    def __init__(
        self,
        *,
        route_groups: RoutePolicyPublicationRepository,
        refresh_runtime: RoutePolicyRuntimeRefresh,
    ) -> None:
        self._route_groups = route_groups
        self._refresh_runtime = refresh_runtime

    async def publish_document(
        self,
        group_key: str,
        document: dict[str, object],
        *,
        published_by: str | None = None,
    ) -> RoutePolicyPublicationResult:
        result = await self._route_groups.publish_policy(
            group_key,
            document,
            published_by=published_by,
        )
        if result is None:
            raise RoutePolicyPublicationNotFoundError("Route group not found")
        return await self._finish(result.policy, result.warnings)

    async def publish_latest_draft(
        self,
        group_key: str,
        *,
        published_by: str | None = None,
    ) -> RoutePolicyPublicationResult:
        policy = await self._route_groups.publish_latest_draft(
            group_key,
            published_by=published_by,
        )
        if policy is None:
            raise RoutePolicyPublicationNotFoundError("Route group or draft policy not found")
        return await self._finish(policy, ())

    async def _finish(
        self,
        policy: RoutePolicyRecord,
        warnings: tuple[str, ...],
    ) -> RoutePolicyPublicationResult:
        refresh_warnings = await self._refresh_runtime()
        return RoutePolicyPublicationResult(
            policy=policy,
            warnings=tuple([*warnings, *refresh_warnings]),
        )
