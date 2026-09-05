from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from src.db.route_policy_lifecycle import RoutePolicyRecord
from src.router.policy_validation import PolicyMemberInventoryItem, validate_route_policy


class RoutePolicyPublicationMember(Protocol):
    deployment_id: str
    enabled: bool


class RoutePolicyPublicationGroup(Protocol):
    mode: str


class RoutePolicyPublicationRepository(Protocol):
    async def get_group(self, group_key: str) -> RoutePolicyPublicationGroup | None: ...

    async def list_members(self, group_key: str) -> Sequence[RoutePolicyPublicationMember]: ...

    async def publish_policy(
        self,
        group_key: str,
        policy_json: dict[str, object],
        *,
        published_by: str | None = None,
    ) -> RoutePolicyRecord | None: ...

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
    """Own policy validation, durable publication, and one post-commit refresh."""

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
        group = await self._require_group(group_key)
        normalized, warnings = validate_route_policy(
            document,
            available_members=await self._member_inventory(group_key),
            workload_mode=group.mode,
        )
        policy = await self._route_groups.publish_policy(
            group_key,
            normalized,
            published_by=published_by,
        )
        if policy is None:
            raise RoutePolicyPublicationNotFoundError("Route group not found")
        return await self._finish(policy, warnings)

    async def publish_latest_draft(
        self,
        group_key: str,
        *,
        published_by: str | None = None,
    ) -> RoutePolicyPublicationResult:
        await self._require_group(group_key)
        policy = await self._route_groups.publish_latest_draft(
            group_key,
            published_by=published_by,
        )
        if policy is None:
            raise RoutePolicyPublicationNotFoundError("Route group or draft policy not found")
        return await self._finish(policy, [])

    async def _require_group(self, group_key: str) -> RoutePolicyPublicationGroup:
        group = await self._route_groups.get_group(group_key)
        if group is None:
            raise RoutePolicyPublicationNotFoundError("Route group not found")
        return group

    async def _member_inventory(self, group_key: str) -> dict[str, PolicyMemberInventoryItem]:
        members = await self._route_groups.list_members(group_key)
        return {
            member.deployment_id.strip(): PolicyMemberInventoryItem(
                deployment_id=member.deployment_id.strip(),
                enabled=member.enabled,
            )
            for member in members
            if member.deployment_id.strip()
        }

    async def _finish(
        self,
        policy: RoutePolicyRecord,
        warnings: list[str],
    ) -> RoutePolicyPublicationResult:
        refresh_warnings = await self._refresh_runtime()
        return RoutePolicyPublicationResult(
            policy=policy,
            warnings=tuple([*warnings, *refresh_warnings]),
        )
