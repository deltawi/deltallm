from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from src.api.admin.endpoints import route_groups
from src.auth.roles import OrganizationRole, PlatformRole
from src.models.platform_auth import PlatformAuthContext
from src.models.route_policy_simulation import RoutePolicySimulationRequest
from src.services.access.managed_asset_access import AssetPrincipal

SCHEDULER_ROUTES = [
    ("GET", "/ui/api/batches/scheduler/model-capacity", None),
    ("GET", "/ui/api/batches/scheduler/status", None),
    ("POST", "/ui/api/batches/scheduler/mode", {"active_mode": "fifo", "shadow_mode": "none"}),
    ("GET", "/ui/api/batches/scheduler/flows", None),
    ("POST", "/ui/api/batches/scheduler/flows/refresh", {}),
    ("POST", "/ui/api/batches/scheduler-dimensions/backfill", {}),
]


def _context(*, org_role: str | None) -> PlatformAuthContext:
    memberships = [{"organization_id": "org-1", "role": org_role}] if org_role else []
    return PlatformAuthContext(
        account_id="acct-1",
        email="user@example.com",
        role=PlatformRole.ORG_USER,
        organization_memberships=memberships,
        team_memberships=[],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), SCHEDULER_ROUTES)
@pytest.mark.parametrize("org_role", [None, OrganizationRole.OWNER])
async def test_scheduler_routes_deny_non_platform_admins(
    client, test_app, monkeypatch, method, path, body, org_role
):
    context = _context(org_role=org_role)
    monkeypatch.setattr(
        "src.middleware.platform_auth.get_platform_auth_context", lambda request: context
    )
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)
    dynamic_config = SimpleNamespace(updates=[])

    async def update_config(update, **kwargs):  # noqa: ANN001, ANN003, ANN202
        dynamic_config.updates.append(update)

    dynamic_config.update_config = update_config
    test_app.state.dynamic_config_manager = dynamic_config

    response = await client.request(method, path, json=body)

    assert response.status_code == 403, response.text
    assert dynamic_config.updates == []


class _PromptRepository:
    async def get_template(self, template_key: str):  # noqa: ANN201
        if template_key == "other-tenant.prompt":
            return SimpleNamespace(prompt_template_id="tmpl-other")
        return None

    async def resolve_prompt(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("simulation must not resolve a prompt the caller cannot read")


class _NoAccess:
    async def get_policy_for_resource(self, kind, resource_id, *, principal):  # noqa: ANN001, ANN201
        del kind, resource_id, principal
        return None


def _request(*, prompt_repository: object | None = None) -> SimpleNamespace:
    state = SimpleNamespace(
        prompt_registry_repository=prompt_repository or _PromptRepository(),
        managed_asset_access_repository=_NoAccess(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


NO_MEMBERSHIP = AssetPrincipal(account_id="acct-1")
PLATFORM_ADMIN = AssetPrincipal(account_id=None, is_platform_admin=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("template_key", ["other-tenant.prompt", "missing.prompt"])
async def test_simulation_prompt_answers_alike_for_hidden_and_missing_prompts(template_key):
    simulation = RoutePolicySimulationRequest(prompt_ref={"template_key": template_key})

    if template_key == "missing.prompt":
        # The simulation service answers "could not be resolved" for unknown prompts.
        await route_groups._require_readable_simulation_prompt(
            _request(), simulation, NO_MEMBERSHIP
        )
        return
    with pytest.raises(HTTPException) as exc_info:
        await route_groups._require_readable_simulation_prompt(
            _request(), simulation, NO_MEMBERSHIP
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "prompt_ref could not be resolved"


@pytest.mark.asyncio
async def test_simulation_prompt_fails_closed_without_access_check():
    resolve_only = SimpleNamespace(resolve_prompt=_PromptRepository().resolve_prompt)
    simulation = RoutePolicySimulationRequest(prompt_ref={"template_key": "other-tenant.prompt"})

    with pytest.raises(HTTPException) as exc_info:
        await route_groups._require_readable_simulation_prompt(
            _request(prompt_repository=resolve_only), simulation, NO_MEMBERSHIP
        )
    assert exc_info.value.status_code == 400

    await route_groups._require_readable_simulation_prompt(
        _request(prompt_repository=resolve_only), simulation, PLATFORM_ADMIN
    )


@pytest.mark.asyncio
async def test_platform_admin_can_simulate_any_prompt():
    simulation = RoutePolicySimulationRequest(prompt_ref={"template_key": "other-tenant.prompt"})

    await route_groups._require_readable_simulation_prompt(_request(), simulation, PLATFORM_ADMIN)


@pytest.mark.asyncio
async def test_default_prompt_inside_metadata_is_access_checked():
    with pytest.raises(HTTPException) as exc_info:
        await route_groups._resolve_group_metadata(
            _request(),
            existing_metadata=None,
            raw_metadata={"default_prompt": {"template_key": "other-tenant.prompt"}},
            raw_default_prompt=...,
            principal=NO_MEMBERSHIP,
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "default_prompt.template_key is invalid or inaccessible"


@pytest.mark.asyncio
async def test_unchanged_default_prompt_in_metadata_is_kept():
    existing = {"default_prompt": {"template_key": "other-tenant.prompt"}, "note": "old"}

    metadata = await route_groups._resolve_group_metadata(
        _request(),
        existing_metadata=existing,
        raw_metadata={"default_prompt": {"template_key": "other-tenant.prompt"}, "note": "new"},
        raw_default_prompt=...,
        principal=NO_MEMBERSHIP,
    )

    assert metadata == {"default_prompt": {"template_key": "other-tenant.prompt"}, "note": "new"}
