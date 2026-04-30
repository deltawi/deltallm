from __future__ import annotations

import logging

import pytest

from src.config import AppConfig, GeneralSettings, Settings
from src.db.callable_target_access_groups import CallableTargetAccessGroupBindingRecord
from src.db.callable_targets import CallableTargetBindingRecord
from src.db.callable_target_policies import CallableTargetScopePolicyRecord
from src.models.responses import UserAPIKeyAuth
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.callable_targets import CallableTarget, DuplicateCallableTargetError, build_callable_target_catalog
from src.services.model_deployments import build_model_registry_from_config
from src.services.model_visibility import (
    filter_visible_models,
    resolve_effective_model_allowlist,
    resolve_model_allowlist_resolution,
)
from src.services.route_groups import route_groups_from_config

_STRONG_TEST_MASTER_KEY = "StrongTestMasterKey2026SecureValue123"


def _test_settings() -> Settings:
    return Settings.model_validate({"master_key": _STRONG_TEST_MASTER_KEY})


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
        sliced = items[offset : offset + limit]
        return sliced, len(items)


class _FakeCallableTargetScopePolicyRepository:
    def __init__(self, policies: list[CallableTargetScopePolicyRecord]) -> None:
        self.policies = list(policies)

    async def list_policies(self, *, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.policies)
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)


class _FakeCallableTargetAccessGroupBindingRepository:
    def __init__(self, bindings: list[CallableTargetAccessGroupBindingRecord]) -> None:
        self.bindings = list(bindings)

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if group_key:
            items = [item for item in items if item.group_key == group_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)


def test_effective_model_allowlist_denies_when_no_explicit_grants_exist() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        models=["gpt-4o-mini", "text-embedding-3-small"],
        team_models=["gpt-4o-mini"],
    )

    assert resolve_effective_model_allowlist(auth) == set()


def test_effective_model_allowlist_denies_without_explicit_scope_bindings() -> None:
    auth = UserAPIKeyAuth(api_key="sk-test")

    assert resolve_effective_model_allowlist(auth) == set()


def test_callable_target_policy_mode_accepts_legacy_config_alias(monkeypatch) -> None:
    monkeypatch.delenv("DELTALLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("DELTALLM_SALT_KEY", raising=False)
    assert GeneralSettings(callable_target_scope_policy_mode="legacy").callable_target_scope_policy_mode == "legacy"
    assert Settings(callable_target_scope_policy_mode="legacy").callable_target_scope_policy_mode == "legacy"


@pytest.mark.asyncio
async def test_effective_model_allowlist_prefers_explicit_callable_target_grants() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        team_id="team-1",
        organization_id="org-1",
        models=["gpt-4o-mini", "text-embedding-3-small"],
    )
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        )
    )
    await service.reload()

    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == {"gpt-4o-mini"}


@pytest.mark.asyncio
async def test_filter_visible_models_applies_user_scope_explicit_grants() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        user_id="user-1",
        organization_id="org-1",
    )
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-user-1",
                    callable_key="gpt-4o-mini",
                    scope_type="user",
                    scope_id="user-1",
                    enabled=True,
                ),
            ]
        )
    )
    await service.reload()

    assert filter_visible_models(
        ["gpt-4o-mini", "text-embedding-3-small"],
        auth,
        callable_target_grant_service=service,
    ) == ["gpt-4o-mini"]


@pytest.mark.asyncio
async def test_effective_model_allowlist_does_not_apply_legacy_user_narrowing_without_explicit_user_bindings() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        user_id="user-1",
        organization_id="org-1",
    )
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
            ]
        )
    )
    await service.reload()

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=service,
        policy_mode="enforce",
    )

    assert resolution.effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_effective_model_allowlist_does_not_narrow_user_scope_when_policy_is_explicitly_inherit() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        user_id="user-1",
        organization_id="org-1",
    )
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-user-1",
                    callable_key="gpt-4o-mini",
                    scope_type="user",
                    scope_id="user-1",
                    enabled=True,
                ),
            ]
        ),
        policy_repository=_FakeCallableTargetScopePolicyRepository(
            [
                CallableTargetScopePolicyRecord(
                    callable_target_scope_policy_id="ctp-user-1",
                    scope_type="user",
                    scope_id="user-1",
                    mode="inherit",
                )
            ]
        ),
    )
    await service.reload()

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=service,
        policy_mode="enforce",
    )

    assert resolution.effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_effective_model_allowlist_treats_disabled_explicit_bindings_as_authoritative() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        organization_id="org-1",
        models=["gpt-4o-mini", "text-embedding-3-small"],
    )
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=False,
                )
            ]
        )
    )
    await service.reload()

    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == set()


@pytest.mark.asyncio
async def test_callable_target_grant_service_loads_scope_policy_modes() -> None:
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        policy_repository=_FakeCallableTargetScopePolicyRepository(
            [
                CallableTargetScopePolicyRecord(
                    callable_target_scope_policy_id="ctp-1",
                    scope_type="team",
                    scope_id="team-1",
                    mode="restrict",
                )
            ]
        ),
    )
    await service.reload()

    assert service.get_scope_mode("team", "team-1") == "restrict"
    assert service.get_scope_mode("api_key", "key-1") is None


@pytest.mark.asyncio
async def test_policy_resolution_restricts_team_and_api_key_scopes() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        team_id="team-1",
        organization_id="org-1",
        models=["gpt-4o-mini", "text-embedding-3-small"],
    )
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-team-1",
                    callable_key="gpt-4o-mini",
                    scope_type="team",
                    scope_id="team-1",
                    enabled=True,
                ),
            ]
        ),
        policy_repository=_FakeCallableTargetScopePolicyRepository(
            [
                CallableTargetScopePolicyRecord(
                    callable_target_scope_policy_id="ctp-team-1",
                    scope_type="team",
                    scope_id="team-1",
                    mode="restrict",
                ),
                CallableTargetScopePolicyRecord(
                    callable_target_scope_policy_id="ctp-key-1",
                    scope_type="api_key",
                    scope_id="sk-test",
                    mode="restrict",
                ),
            ]
        ),
    )
    await service.reload()

    resolution = resolve_model_allowlist_resolution(
        auth,
        callable_target_grant_service=service,
        policy_mode="enforce",
    )

    assert resolution.policy_authoritative is True
    assert resolution.policy_allowlist == set()
    assert resolution.effective_allowlist == set()


@pytest.mark.asyncio
async def test_policy_resolution_expands_organization_access_group_grants() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        organization_id="org-1",
    )
    catalog = {
        "gpt-4o-mini": CallableTarget(
            key="gpt-4o-mini",
            target_type="model",
            access_groups=frozenset({"beta"}),
        ),
        "text-embedding-3-small": CallableTarget(
            key="text-embedding-3-small",
            target_type="model",
            access_groups=frozenset({"embedding"}),
        ),
    }
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        access_group_repository=_FakeCallableTargetAccessGroupBindingRepository(
            [
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-org-1",
                    group_key="beta",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        ),
        callable_target_catalog_getter=lambda: catalog,
    )
    await service.reload()

    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == {
        "gpt-4o-mini"
    }


@pytest.mark.asyncio
async def test_policy_resolution_intersects_restricted_team_group_grants() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        team_id="team-1",
        organization_id="org-1",
    )
    catalog = {
        "gpt-4o-mini": CallableTarget(
            key="gpt-4o-mini",
            target_type="model",
            access_groups=frozenset({"beta", "chat"}),
        ),
        "text-embedding-3-small": CallableTarget(
            key="text-embedding-3-small",
            target_type="model",
            access_groups=frozenset({"beta", "embedding"}),
        ),
    }
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        access_group_repository=_FakeCallableTargetAccessGroupBindingRepository(
            [
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-org-1",
                    group_key="beta",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-team-1",
                    group_key="chat",
                    scope_type="team",
                    scope_id="team-1",
                    enabled=True,
                ),
            ]
        ),
        policy_repository=_FakeCallableTargetScopePolicyRepository(
            [
                CallableTargetScopePolicyRecord(
                    callable_target_scope_policy_id="ctp-team-1",
                    scope_type="team",
                    scope_id="team-1",
                    mode="restrict",
                )
            ]
        ),
        callable_target_catalog_getter=lambda: catalog,
    )
    await service.reload()

    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == {
        "gpt-4o-mini"
    }


@pytest.mark.asyncio
async def test_disabled_group_binding_is_authoritative_without_granting_models() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        organization_id="org-1",
    )
    catalog = {
        "gpt-4o-mini": CallableTarget(
            key="gpt-4o-mini",
            target_type="model",
            access_groups=frozenset({"beta"}),
        )
    }
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        access_group_repository=_FakeCallableTargetAccessGroupBindingRepository(
            [
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-org-1",
                    group_key="beta",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=False,
                )
            ]
        ),
        callable_target_catalog_getter=lambda: catalog,
    )
    await service.reload()

    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == set()


@pytest.mark.asyncio
async def test_group_grant_picks_up_future_matching_models_after_reload() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        organization_id="org-1",
    )
    catalog: dict[str, CallableTarget] = {}
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        access_group_repository=_FakeCallableTargetAccessGroupBindingRepository(
            [
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-org-1",
                    group_key="future",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        ),
        callable_target_catalog_getter=lambda: catalog,
    )
    await service.reload()
    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == set()

    catalog["future-model"] = CallableTarget(
        key="future-model",
        target_type="model",
        access_groups=frozenset({"future"}),
    )
    await service.reload()

    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == {
        "future-model"
    }


@pytest.mark.asyncio
async def test_user_group_grants_restrict_by_default() -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        user_id="user-1",
        organization_id="org-1",
    )
    catalog = {
        "gpt-4o-mini": CallableTarget(
            key="gpt-4o-mini",
            target_type="model",
            access_groups=frozenset({"chat"}),
        ),
        "text-embedding-3-small": CallableTarget(
            key="text-embedding-3-small",
            target_type="model",
            access_groups=frozenset({"embedding"}),
        ),
    }
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        access_group_repository=_FakeCallableTargetAccessGroupBindingRepository(
            [
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-org-1",
                    group_key="chat",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-org-2",
                    group_key="embedding",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-user-1",
                    group_key="chat",
                    scope_type="user",
                    scope_id="user-1",
                    enabled=True,
                ),
            ]
        ),
        callable_target_catalog_getter=lambda: catalog,
    )
    await service.reload()

    assert filter_visible_models(
        ["gpt-4o-mini", "text-embedding-3-small"],
        auth,
        callable_target_grant_service=service,
    ) == ["gpt-4o-mini"]


@pytest.mark.asyncio
async def test_shadow_mode_logs_mismatch_while_preserving_legacy_enforcement(caplog: pytest.LogCaptureFixture) -> None:
    auth = UserAPIKeyAuth(
        api_key="sk-test",
        team_id="team-1",
        organization_id="org-1",
        models=["gpt-4o-mini", "text-embedding-3-small"],
        team_models=["gpt-4o-mini", "text-embedding-3-small"],
    )
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-team-1",
                    callable_key="gpt-4o-mini",
                    scope_type="team",
                    scope_id="team-1",
                    enabled=True,
                ),
            ]
        ),
        policy_repository=_FakeCallableTargetScopePolicyRepository(
            [
                CallableTargetScopePolicyRecord(
                    callable_target_scope_policy_id="ctp-team-1",
                    scope_type="team",
                    scope_id="team-1",
                    mode="restrict",
                )
            ]
        ),
    )
    await service.reload()

    with caplog.at_level(logging.INFO):
        resolution = resolve_model_allowlist_resolution(
            auth,
            callable_target_grant_service=service,
            policy_mode="shadow",
            emit_shadow_log=True,
        )

    assert resolution.effective_allowlist == {"gpt-4o-mini", "text-embedding-3-small"}
    assert resolution.policy_allowlist == {"gpt-4o-mini"}
    assert resolution.shadow_mismatch is True
    assert "callable_target_policy_shadow_mismatch" in caplog.text
    assert caplog.records[-1].difference_type == "removed_only"
    assert caplog.records[-1].removed_models == ["text-embedding-3-small"]


@pytest.mark.asyncio
async def test_v1_models_filters_by_effective_allowlist(client, test_app) -> None:
    record = next(iter(test_app.state._test_repo.records.values()))
    record.user_id = "user-1"
    test_app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id="org-default",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-default",
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-user-1",
                    callable_key="gpt-4o-mini",
                    scope_type="user",
                    scope_id="user-1",
                    enabled=True,
                ),
            ]
        )
    )
    await test_app.state.callable_target_grant_service.reload()

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.get("/v1/models", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["gpt-4o-mini"]


@pytest.mark.asyncio
async def test_v1_models_uses_explicit_callable_target_grants_when_present(client, test_app) -> None:
    record = next(iter(test_app.state._test_repo.records.values()))
    record.organization_id = "org-1"
    test_app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-1",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        )
    )
    await test_app.state.callable_target_grant_service.reload()

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.get("/v1/models", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["text-embedding-3-small"]


@pytest.mark.asyncio
async def test_v1_models_uses_access_group_grants(client, test_app) -> None:
    record = next(iter(test_app.state._test_repo.records.values()))
    record.organization_id = "org-1"
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(
            key="gpt-4o-mini",
            target_type="model",
            access_groups=frozenset({"beta"}),
        ),
        "text-embedding-3-small": CallableTarget(
            key="text-embedding-3-small",
            target_type="model",
            access_groups=frozenset({"embedding"}),
        ),
    }
    test_app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        access_group_repository=_FakeCallableTargetAccessGroupBindingRepository(
            [
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-1",
                    group_key="beta",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        ),
        callable_target_catalog_getter=lambda: test_app.state.callable_target_catalog,
    )
    await test_app.state.callable_target_grant_service.reload()

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.get("/v1/models", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["gpt-4o-mini"]


@pytest.mark.asyncio
async def test_v1_models_user_scope_explicit_grants_apply_without_legacy_api_key_scope(client, test_app) -> None:
    record = next(iter(test_app.state._test_repo.records.values()))
    record.user_id = "user-1"
    record.team_id = None
    record.organization_id = None
    record.models = ["gpt-4o-mini", "text-embedding-3-small"]
    test_app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-1",
                    callable_key="gpt-4o-mini",
                    scope_type="user",
                    scope_id="user-1",
                    enabled=True,
                )
            ]
        )
    )
    await test_app.state.callable_target_grant_service.reload()

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.get("/v1/models", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["gpt-4o-mini"]


def test_callable_target_catalog_includes_live_route_groups() -> None:
    catalog = build_callable_target_catalog(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-1", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=[
            {
                "key": "support-fast",
                "enabled": True,
                "members": [{"deployment_id": "dep-1", "enabled": True}],
            }
        ],
    )

    assert set(catalog) == {"gpt-4o-mini", "support-fast"}
    assert catalog["support-fast"].target_type == "route_group"


def test_callable_target_catalog_includes_model_access_groups() -> None:
    catalog = build_callable_target_catalog(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-1",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"access_groups": ["Beta", "support", "beta"]},
                }
            ]
        }
    )

    assert catalog["gpt-4o-mini"].access_groups == frozenset({"beta", "support"})


def test_callable_target_catalog_disables_access_groups_for_conflicting_public_model(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        catalog = build_callable_target_catalog(
            {
                "shared-model": [
                    {
                        "deployment_id": "dep-1",
                        "deltallm_params": {"model": "openai/gpt-4o-mini"},
                        "model_info": {"access_groups": ["beta"]},
                    },
                    {
                        "deployment_id": "dep-2",
                        "deltallm_params": {"model": "azure/gpt-4o-mini"},
                        "model_info": {"access_groups": ["stable"]},
                    },
                ]
            }
        )

    assert catalog["shared-model"].access_groups == frozenset()
    assert "access_groups conflict" in caplog.text


def test_callable_target_catalog_includes_route_group_access_groups() -> None:
    catalog = build_callable_target_catalog(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-1", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=[
            {
                "key": "support-fast",
                "enabled": True,
                "access_groups": ["support"],
                "members": [{"deployment_id": "dep-1", "enabled": True}],
            }
        ],
    )

    assert catalog["support-fast"].access_groups == frozenset({"support"})


@pytest.mark.asyncio
async def test_config_loaded_model_access_groups_expand_group_grants() -> None:
    cfg = AppConfig.model_validate(
        {
            "model_list": [
                {
                    "deployment_id": "dep-1",
                    "model_name": "gpt-4o-mini",
                    "deltallm_params": {
                        "model": "openai/gpt-4o-mini",
                        "api_key": "provider-key",
                        "api_base": "https://api.openai.com/v1",
                    },
                    "model_info": {"access_groups": ["Beta"]},
                }
            ]
        }
    )
    registry = await build_model_registry_from_config(cfg, _test_settings())
    catalog = build_callable_target_catalog(registry)
    auth = UserAPIKeyAuth(api_key="sk-test", organization_id="org-1")
    service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository([]),
        access_group_repository=_FakeCallableTargetAccessGroupBindingRepository(
            [
                CallableTargetAccessGroupBindingRecord(
                    callable_target_access_group_binding_id="ctagb-org-1",
                    group_key="beta",
                    scope_type="organization",
                    scope_id="org-1",
                    enabled=True,
                )
            ]
        ),
        callable_target_catalog_getter=lambda: catalog,
    )
    await service.reload()

    assert catalog["gpt-4o-mini"].access_groups == frozenset({"beta"})
    assert resolve_effective_model_allowlist(auth, callable_target_grant_service=service) == {
        "gpt-4o-mini"
    }


@pytest.mark.asyncio
async def test_config_loaded_route_group_access_groups_reach_callable_catalog() -> None:
    cfg = AppConfig.model_validate(
        {
            "model_list": [
                {
                    "deployment_id": "dep-1",
                    "model_name": "gpt-4o-mini",
                    "deltallm_params": {
                        "model": "openai/gpt-4o-mini",
                        "api_key": "provider-key",
                        "api_base": "https://api.openai.com/v1",
                    },
                }
            ],
            "router_settings": {
                "route_groups": [
                    {
                        "key": "support-fast",
                        "access_groups": ["Support"],
                        "members": [{"deployment_id": "dep-1"}],
                    }
                ]
            },
        }
    )
    registry = await build_model_registry_from_config(cfg, _test_settings())
    catalog = build_callable_target_catalog(registry, route_groups=route_groups_from_config(cfg))

    assert catalog["support-fast"].access_groups == frozenset({"support"})


def test_callable_target_catalog_rejects_model_and_route_group_key_collision() -> None:
    with pytest.raises(DuplicateCallableTargetError):
        build_callable_target_catalog(
            {
                "shared-name": [
                    {"deployment_id": "dep-1", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
                ]
            },
            route_groups=[
                {
                    "key": "shared-name",
                    "enabled": True,
                    "members": [{"deployment_id": "dep-1", "enabled": True}],
                }
            ],
        )


@pytest.mark.asyncio
async def test_v1_models_lists_callable_route_groups(client, test_app) -> None:
    record = next(iter(test_app.state._test_repo.records.values()))
    test_app.state.route_groups = [
        {
            "key": "support-fast",
            "enabled": True,
            "members": [{"deployment_id": "gpt-4o-mini-0", "enabled": True}],
        }
    ]
    test_app.state.callable_target_catalog = build_callable_target_catalog(
        test_app.state.model_registry,
        test_app.state.route_groups,
    )
    test_app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=_FakeCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-1",
                    callable_key="gpt-4o-mini",
                    scope_type="organization",
                    scope_id=str(record.organization_id),
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-2",
                    callable_key="text-embedding-3-small",
                    scope_type="organization",
                    scope_id=str(record.organization_id),
                    enabled=True,
                ),
                CallableTargetBindingRecord(
                    callable_target_binding_id="ctb-org-3",
                    callable_key="support-fast",
                    scope_type="organization",
                    scope_id=str(record.organization_id),
                    enabled=True,
                ),
            ]
        )
    )
    await test_app.state.callable_target_grant_service.reload()

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.get("/v1/models", headers=headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [
        "gpt-4o-mini",
        "support-fast",
        "text-embedding-3-small",
    ]
