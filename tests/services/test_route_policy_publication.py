from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.db.route_policy_lifecycle import RoutePolicyRecord
from src.services.route_policy_publication import (
    RoutePolicyPublicationNotFoundError,
    RoutePolicyPublicationService,
)


@dataclass
class _Member:
    deployment_id: str
    enabled: bool = True


class _Repository:
    def __init__(self) -> None:
        self.group_exists = True
        self.members = [_Member("dep-a")]
        self.document_calls: list[dict[str, object]] = []
        self.draft_calls = 0
        self.draft_exists = True

    async def get_group(self, group_key: str) -> object | None:
        del group_key
        return object() if self.group_exists else None

    async def list_members(self, group_key: str) -> list[_Member]:
        del group_key
        return list(self.members)

    async def publish_policy(
        self,
        group_key: str,
        policy_json: dict[str, object],
        *,
        published_by: str | None = None,
    ) -> RoutePolicyRecord:
        self.document_calls.append(policy_json)
        return _policy(group_key, policy_json, published_by=published_by)

    async def publish_latest_draft(
        self,
        group_key: str,
        *,
        published_by: str | None = None,
    ) -> RoutePolicyRecord | None:
        self.draft_calls += 1
        if not self.draft_exists:
            return None
        return _policy(group_key, {"strategy": "weighted"}, published_by=published_by)


def _policy(
    group_key: str,
    document: dict[str, object],
    *,
    published_by: str | None,
) -> RoutePolicyRecord:
    return RoutePolicyRecord(
        route_policy_id="policy-1",
        route_group_id=group_key,
        version=1,
        status="published",
        policy_json=document,
        semantics_version=2,
        published_by=published_by,
    )


@pytest.mark.asyncio
async def test_document_publication_normalizes_alias_and_refreshes_once() -> None:
    repository = _Repository()
    refresh_calls = 0

    async def refresh() -> tuple[str, ...]:
        nonlocal refresh_calls
        refresh_calls += 1
        return ("refresh warning",)

    service = RoutePolicyPublicationService(
        route_groups=repository,
        refresh_runtime=refresh,
    )

    result = await service.publish_document(
        "support",
        {"mode": "fallback"},
        published_by="admin_api",
    )

    assert repository.document_calls == [{"strategy": "priority-based-routing"}]
    assert repository.draft_calls == 0
    assert result.policy.published_by == "admin_api"
    assert result.warnings == (
        "Policy mode 'fallback' is deprecated; use strategy 'priority-based-routing'.",
        "refresh warning",
    )
    assert refresh_calls == 1


@pytest.mark.asyncio
async def test_empty_document_is_distinct_from_latest_draft() -> None:
    repository = _Repository()

    async def refresh() -> tuple[str, ...]:
        return ()

    service = RoutePolicyPublicationService(
        route_groups=repository,
        refresh_runtime=refresh,
    )

    await service.publish_document("support", {})
    await service.publish_latest_draft("support")

    assert repository.document_calls == [{}]
    assert repository.draft_calls == 1


@pytest.mark.asyncio
async def test_missing_group_or_draft_does_not_refresh() -> None:
    repository = _Repository()
    refresh_calls = 0

    async def refresh() -> tuple[str, ...]:
        nonlocal refresh_calls
        refresh_calls += 1
        return ()

    service = RoutePolicyPublicationService(
        route_groups=repository,
        refresh_runtime=refresh,
    )

    repository.group_exists = False
    with pytest.raises(RoutePolicyPublicationNotFoundError, match="Route group not found"):
        await service.publish_document("missing", {})

    repository.group_exists = True
    repository.draft_exists = False
    with pytest.raises(RoutePolicyPublicationNotFoundError, match="draft policy not found"):
        await service.publish_latest_draft("support")

    assert refresh_calls == 0
