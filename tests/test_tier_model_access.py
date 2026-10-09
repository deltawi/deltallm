from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from src.batch.endpoints import BATCH_ENDPOINT_CHAT_COMPLETIONS
from src.batch.request_validation import parse_batch_input_line
from src.db.callable_targets import CallableTargetBindingRecord
from src.db.callable_target_policies import CallableTargetScopePolicyRecord
from src.models.errors import PermissionDeniedError
from src.models.responses import UserAPIKeyAuth
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.model_visibility import (
    ensure_model_allowed,
    resolve_effective_model_allowlist,
    resolve_model_allowlist_resolution,
)
from src.services.tier_policy_service import resolve_tier_policy_unavailable_decision


class _FakeCallableTargetBindingRepository:
    def __init__(self, bindings: list[CallableTargetBindingRecord]) -> None:
        self.bindings = list(bindings)

    async def list_bindings(self, *, callable_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if callable_key:
            items = [item for item in items if item.callable_key == callable_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        return items[offset : offset + limit], len(items)


class _FakeCallableTargetScopePolicyRepository:
    def __init__(self, policies: list[CallableTargetScopePolicyRecord]) -> None:
        self.policies = list(policies)

    async def list_policies(self, *, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.policies)
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        return items[offset : offset + limit], len(items)


@dataclass(frozen=True, slots=True)
class _FakeTierModelPolicy:
    access_mode: str


class _FakeTierSnapshotInfo:
    etag = "fake-tier-snapshot"
    org_count = 1


class _FakeTierPolicyService:
    def __init__(
        self,
        *,
        allowed_by_org: dict[str, set[str] | frozenset[str]] | None = None,
        denied: set[tuple[str, str]] | None = None,
        explicit_orgs: set[str] | None = None,
        mode: str = "enforce",
        missing_service_mode: str = "fail_open",
        snapshot_stale: bool = False,
    ) -> None:
        self.allowed_by_org = {
            org_id: frozenset(values)
            for org_id, values in (allowed_by_org or {}).items()
        }
        self.denied = set(denied or set())
        self.explicit_orgs = set(explicit_orgs or set(self.allowed_by_org))
        self.mode = mode
        self.missing_service_mode = missing_service_mode
        self.snapshot_stale = snapshot_stale

    def has_explicit_tier_policy(self, organization_id: str | None) -> bool:
        return str(organization_id or "").strip() in self.explicit_orgs

    def resolve_unavailable_decision(self, organization_id: str | None) -> object:
        return resolve_tier_policy_unavailable_decision(
            self,
            organization_id,
            mode=self.mode,
            missing_service_mode=self.missing_service_mode,
        )

    def resolve_org_allowed_callable_keys(
        self,
        organization_id: str | None,
    ) -> frozenset[str] | None:
        normalized = str(organization_id or "").strip()
        if normalized not in self.explicit_orgs:
            return None
        return self.allowed_by_org.get(normalized, frozenset())

    def get_model_policy(
        self,
        organization_id: str | None,
        callable_key: str | None,
    ) -> _FakeTierModelPolicy | None:
        key = (str(organization_id or "").strip(), str(callable_key or "").strip())
        if key in self.denied:
            return _FakeTierModelPolicy("deny")
        return None

    def snapshot_info(self) -> _FakeTierSnapshotInfo:
        return _FakeTierSnapshotInfo()


async def _loaded_grant_service(
    *,
    bindings: list[CallableTargetBindingRecord],
    policies: list[CallableTargetScopePolicyRecord] | None = None,
) -> CallableTargetGrantService:
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(bindings),
        policy_repository=_FakeCallableTargetScopePolicyRepository(policies or []),
    )
    await service.reload()
    return service


def _binding(
    record_id: str,
    *,
    callable_key: str,
    scope_type: str = "organization",
    scope_id: str = "org-1",
) -> CallableTargetBindingRecord:
    return CallableTargetBindingRecord(
        callable_target_binding_id=record_id,
        callable_key=callable_key,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=True,
    )


def _scope_policy(
    record_id: str,
    *,
    scope_type: str,
    scope_id: str,
    mode: str,
) -> CallableTargetScopePolicyRecord:
    return CallableTargetScopePolicyRecord(
        callable_target_scope_policy_id=record_id,
        scope_type=scope_type,
        scope_id=scope_id,
        mode=mode,
    )


def _org_model_bindings() -> list[CallableTargetBindingRecord]:
    return [
        _binding("ctb-org-1", callable_key="gpt-4o-mini"),
        _binding("ctb-org-2", callable_key="text-embedding-3-small"),
    ]


@pytest.mark.asyncio
async def test_tier_policy_primary_access_grants_without_callable_target_org_bindings() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(bindings=[])
    tier_service = _FakeTierPolicyService(allowed_by_org={"org-1": {"gpt-4o-mini"}})

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
    )

    assert resolution.pre_tier_allowlist == set()
    assert resolution.tier_allowlist == {"gpt-4o-mini"}
    assert resolution.effective_allowlist == {"gpt-4o-mini"}


@pytest.mark.asyncio
async def test_tier_policy_add_on_union_allows_multiple_models() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(bindings=[])
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini", "text-embedding-3-small"}},
    )

    assert resolve_effective_model_allowlist(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
    ) == {"gpt-4o-mini", "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_tier_policy_without_explicit_org_leaves_access_unchanged() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(bindings=_org_model_bindings())
    tier_service = _FakeTierPolicyService()

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
    )

    assert resolution.tier_applied is False
    assert resolution.effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_tier_policy_deny_rule_wins_and_logs_denial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(bindings=[])
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"text-embedding-3-small"}},
        denied={("org-1", "text-embedding-3-small")},
    )

    with caplog.at_level(logging.INFO), pytest.raises(PermissionDeniedError):
        ensure_model_allowed(
            auth,
            "text-embedding-3-small",
            callable_target_grant_service=grant_service,
            tier_policy_service=tier_service,
        )

    assert "tier_policy_model_access_denied" in caplog.text
    assert caplog.records[-1].reason == "tier_policy_deny"


@pytest.mark.asyncio
async def test_tier_policy_allowlist_exclusion_logs_without_callable_target_org_bindings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(bindings=[])
    tier_service = _FakeTierPolicyService(allowed_by_org={"org-1": {"gpt-4o-mini"}})

    with caplog.at_level(logging.INFO), pytest.raises(PermissionDeniedError):
        ensure_model_allowed(
            auth,
            "text-embedding-3-small",
            callable_target_grant_service=grant_service,
            tier_policy_service=tier_service,
        )

    assert "tier_policy_model_access_denied" in caplog.text
    assert caplog.records[-1].reason == "tier_policy_allowlist_excluded"


@pytest.mark.asyncio
async def test_tier_policy_intersects_restricted_team_scope() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", team_id="team-1", organization_id="org-1")
    grant_service = await _loaded_grant_service(
        bindings=[
            _binding(
                "ctb-team-1",
                callable_key="gpt-4o-mini",
                scope_type="team",
                scope_id="team-1",
            ),
        ],
        policies=[
            _scope_policy(
                "ctp-team-1",
                scope_type="team",
                scope_id="team-1",
                mode="restrict",
            )
        ],
    )
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini", "text-embedding-3-small"}},
    )

    assert resolve_effective_model_allowlist(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
    ) == {"gpt-4o-mini"}


@pytest.mark.asyncio
async def test_tier_policy_intersects_restricted_api_key_scope() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(
        bindings=[
            _binding(
                "ctb-key-1",
                callable_key="text-embedding-3-small",
                scope_type="api_key",
                scope_id="sk-test",
            ),
        ],
        policies=[
            _scope_policy(
                "ctp-key-1",
                scope_type="api_key",
                scope_id="sk-test",
                mode="restrict",
            )
        ],
    )
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini", "text-embedding-3-small"}},
    )

    assert resolve_effective_model_allowlist(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
    ) == {"text-embedding-3-small"}


@pytest.mark.asyncio
async def test_tier_policy_intersects_default_restricted_user_scope() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        organization_id="org-1",
        user_id="user-1",
    )
    grant_service = await _loaded_grant_service(
        bindings=[
            _binding(
                "ctb-user-1",
                callable_key="gpt-4o-mini",
                scope_type="user",
                scope_id="user-1",
            ),
        ],
    )
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini", "text-embedding-3-small"}},
    )

    assert resolve_effective_model_allowlist(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
    ) == {"gpt-4o-mini"}


@pytest.mark.asyncio
async def test_tier_enforce_does_not_apply_direct_restricts_when_callable_policy_shadow() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        team_id="team-1",
        organization_id="org-1",
        models=["gpt-4o-mini", "text-embedding-3-small"],
        team_models=["gpt-4o-mini", "text-embedding-3-small"],
    )
    grant_service = await _loaded_grant_service(
        bindings=[
            _binding(
                "ctb-team-1",
                callable_key="gpt-4o-mini",
                scope_type="team",
                scope_id="team-1",
            ),
        ],
        policies=[
            _scope_policy(
                "ctp-team-1",
                scope_type="team",
                scope_id="team-1",
                mode="restrict",
            )
        ],
    )
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini", "text-embedding-3-small"}},
    )

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
        policy_mode="shadow",
    )

    assert resolution.policy_mode == "shadow"
    assert resolution.pre_tier_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}
    assert resolution.tier_effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}
    assert resolution.effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_tier_policy_shadow_logs_mismatch_without_enforcing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(bindings=_org_model_bindings())
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini"}},
        mode="shadow",
    )

    with caplog.at_level(logging.INFO):
        resolution = resolve_model_allowlist_resolution(
            auth,
            callable_target_grant_service=grant_service,
            tier_policy_service=tier_service,
            emit_shadow_log=True,
        )

    assert resolution.effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}
    assert resolution.tier_effective_allowlist == {"gpt-4o-mini"}
    assert resolution.tier_shadow_mismatch is True
    assert "tier_policy_shadow_mismatch" in caplog.text


@pytest.mark.asyncio
async def test_tier_policy_fail_closed_unavailable_blocks_explicit_org() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(
        bindings=[_binding("ctb-org-1", callable_key="gpt-4o-mini")]
    )
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini"}},
        missing_service_mode="fail_closed",
        snapshot_stale=True,
    )

    with pytest.raises(PermissionDeniedError):
        ensure_model_allowed(
            auth,
            "gpt-4o-mini",
            callable_target_grant_service=grant_service,
            tier_policy_service=tier_service,
        )


@pytest.mark.asyncio
async def test_tier_policy_fail_open_unavailable_preserves_access() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(bindings=_org_model_bindings())
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini"}},
        missing_service_mode="fail_open",
        snapshot_stale=True,
    )

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=tier_service,
    )

    assert resolution.tier_applied is False
    assert resolution.tier_authoritative is False
    assert resolution.effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_tier_policy_missing_service_fail_closed_blocks_when_enforced() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(
        bindings=[_binding("ctb-org-1", callable_key="gpt-4o-mini")]
    )

    with pytest.raises(PermissionDeniedError):
        ensure_model_allowed(
            auth,
            "gpt-4o-mini",
            callable_target_grant_service=grant_service,
            tier_policy_service=None,
            tier_policy_mode="enforce",
            tier_policy_missing_service_mode="fail_closed",
        )


@pytest.mark.asyncio
async def test_tier_policy_missing_service_fail_open_preserves_access() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(
        bindings=[_binding("ctb-org-1", callable_key="gpt-4o-mini")]
    )

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=grant_service,
        tier_policy_service=None,
        tier_policy_mode="enforce",
        tier_policy_missing_service_mode="fail_open",
    )

    assert resolution.tier_authoritative is False
    assert resolution.tier_unavailable_reason == "tier_policy_unavailable_fail_open"
    assert resolution.effective_allowlist == {"gpt-4o-mini"}


@pytest.mark.asyncio
async def test_master_key_bypasses_tier_policy_access() -> None:
    auth = UserAPIKeyAuth(api_key="master_key", organization_id="org-1")
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": set()},
        denied={("org-1", "gpt-4o-mini")},
    )

    assert resolve_effective_model_allowlist(auth, tier_policy_service=tier_service) is None
    ensure_model_allowed(auth, "gpt-4o-mini", tier_policy_service=tier_service)


@pytest.mark.asyncio
async def test_v1_models_filters_by_tier_policy(client, test_app) -> None:
    record = next(iter(test_app.state._test_repo.records.values()))
    record.organization_id = "org-1"
    test_app.state.callable_target_grant_service = await _loaded_grant_service(
        bindings=[]
    )
    test_app.state.tier_policy_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"gpt-4o-mini"}},
    )

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.get("/v1/models", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["gpt-4o-mini"]


@pytest.mark.asyncio
async def test_batch_input_line_enforces_tier_policy() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    grant_service = await _loaded_grant_service(
        bindings=[_binding("ctb-org-1", callable_key="gpt-4o-mini")]
    )
    tier_service = _FakeTierPolicyService(
        allowed_by_org={"org-1": {"text-embedding-3-small"}},
    )
    raw_line = (
        '{"custom_id":"req-1","method":"POST","url":"/v1/chat/completions",'
        '"body":{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}}'
    )

    with pytest.raises(HTTPException) as exc_info:
        parse_batch_input_line(
            raw_line,
            line_number=1,
            endpoint=BATCH_ENDPOINT_CHAT_COMPLETIONS,
            auth=auth,
            seen_custom_ids=set(),
            callable_target_grant_service=grant_service,
            callable_target_scope_policy_mode="enforce",
            tier_policy_service=tier_service,
        )

    assert exc_info.value.status_code == 403
