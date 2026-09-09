from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.db.route_policy_lifecycle import (
    RoutePolicyRecord,
    RoutePolicyWriteResult,
    RoutePolicyValidationContext,
)
from src.db.route_groups import RouteGroupRepository
from src.router.policy_validation import CURRENT_POLICY_SEMANTICS_VERSION, PolicyMemberInventoryItem
from src.router.selection.policy import ensure_selector_activation_supported
from src.services.route_policy_publication import (
    RoutePolicyPublicationNotFoundError,
    RoutePolicyPublicationService,
)


@dataclass
class _Member:
    deployment_id: str
    enabled: bool = True


@dataclass
class _Group:
    mode: str = "chat"


class _Repository:
    def __init__(self) -> None:
        self.group_exists = True
        self.members = [_Member("dep-a")]
        self.document_calls: list[dict[str, object]] = []
        self.draft_calls = 0
        self.draft_exists = True
        self.group_mode = "chat"
        self.deployment_modes = {"dep-a": "chat", "dep-b": "chat"}

    async def get_group(self, group_key: str) -> _Group | None:
        del group_key
        return _Group(mode=self.group_mode) if self.group_exists else None

    async def list_members(self, group_key: str) -> list[_Member]:
        del group_key
        return list(self.members)

    async def publish_policy(
        self,
        group_key: str,
        policy_json: dict[str, object],
        *,
        published_by: str | None = None,
    ) -> RoutePolicyWriteResult | None:
        if not self.group_exists:
            return None
        prepared = RouteGroupRepository(None)._prepare_policy_write(
            policy_json,
            current=None,
            context=RoutePolicyValidationContext(
                group_key=group_key,
                group_mode=self.group_mode,
                inventory={
                    member.deployment_id: PolicyMemberInventoryItem(
                        member.deployment_id,
                        member.enabled,
                        self.deployment_modes.get(member.deployment_id),
                    )
                    for member in self.members
                },
            ),
        )
        ensure_selector_activation_supported(
            prepared.normalized, semantics_version=CURRENT_POLICY_SEMANTICS_VERSION
        )
        self.document_calls.append(policy_json)
        return RoutePolicyWriteResult(
            _policy(group_key, prepared.document, published_by=published_by), prepared.warnings
        )

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
        semantics_version=CURRENT_POLICY_SEMANTICS_VERSION,
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

    assert repository.document_calls == [{"mode": "fallback"}]
    assert result.policy.policy_json == {"strategy": "priority-based-routing"}
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
async def test_selector_publication_is_rejected_before_repository_or_refresh() -> None:
    repository = _Repository()
    repository.members.append(_Member("dep-b"))
    refresh_calls = 0

    async def refresh() -> tuple[str, ...]:
        nonlocal refresh_calls
        refresh_calls += 1
        return ()

    service = RoutePolicyPublicationService(
        route_groups=repository,
        refresh_runtime=refresh,
    )

    with pytest.raises(ValueError, match="unknown_capacity=exclude"):
        await service.publish_document(
            "support",
            {
                "selector": {
                    "kind": "llm-tier",
                    "classifier_deployment_id": "dep-a",
                    "lanes": [
                        {"id": "economy", "rank": 0, "description": "Routine work"},
                        {"id": "quality", "rank": 1, "description": "Complex work"},
                    ],
                },
                "members": [
                    {"deployment_id": "dep-a", "lane": "economy"},
                    {"deployment_id": "dep-b", "lane": "quality"},
                ],
            },
        )

    assert repository.document_calls == []
    assert refresh_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("classifier_mode", [None, "embedding"])
async def test_selector_publication_rejects_unknown_or_nonchat_classifier_mode(
    classifier_mode: str | None,
) -> None:
    repository = _Repository()
    repository.members.append(_Member("dep-b"))

    async def refresh() -> tuple[str, ...]:
        return ()

    deployment_modes = {"dep-b": "chat"}
    if classifier_mode is not None:
        deployment_modes["dep-a"] = classifier_mode
    repository.deployment_modes = deployment_modes
    service = RoutePolicyPublicationService(
        route_groups=repository,
        refresh_runtime=refresh,
    )

    with pytest.raises(ValueError, match="classifier must reference a chat deployment"):
        await service.publish_document(
            "support",
            {
                "selector": {
                    "kind": "llm-tier",
                    "classifier_deployment_id": "dep-a",
                    "lanes": [
                        {"id": "economy", "rank": 0, "description": "Routine work"},
                        {"id": "quality", "rank": 1, "description": "Complex work"},
                    ],
                },
                "members": [
                    {"deployment_id": "dep-a", "lane": "economy"},
                    {"deployment_id": "dep-b", "lane": "quality"},
                ],
            },
        )

    assert repository.document_calls == []


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


@pytest.mark.asyncio
async def test_document_publication_rejects_context_for_unsupported_group_mode() -> None:
    repository = _Repository()
    repository.group_mode = "rerank"

    async def refresh() -> tuple[str, ...]:
        return ()

    service = RoutePolicyPublicationService(
        route_groups=repository,
        refresh_runtime=refresh,
    )

    with pytest.raises(ValueError, match="route group mode 'rerank'"):
        await service.publish_document(
            "support",
            {"context": {"mode": "eligible-only"}},
        )

    assert repository.document_calls == []
