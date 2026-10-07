from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request
import pytest

from src.api.admin.endpoints.common import get_auth_scope
from src.api.admin.endpoints.teams import _require_team_access
from src.auth.external_client import ExternalClientResolver
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_policy import CUSTOMER_PERMISSION_CEILING
from src.auth.roles import Permission
from src.middleware.admin import (
    require_authenticated,
    require_master_key,
    require_admin_permission,
    require_any_admin_permission,
)
from src.middleware.platform_auth import (
    has_scoped_permission,
    has_platform_admin_session,
    has_master_key_session,
    require_platform_permission,
)
from src.models.external_auth import ExternalWorkspaceContext
from src.models.platform_auth import PlatformAuthContext
from src.services.master_session_service import MasterSessionStatus
from src.services.ui_authorization import effective_permissions_for_context, build_ui_access


def customer() -> PlatformAuthContext:
    return PlatformAuthContext(
        account_id="customer",
        email="customer@example.com",
        role="platform_admin",
        permissions=[
            *CUSTOMER_PERMISSION_CEILING,
            Permission.PLATFORM_ADMIN,
            Permission.KEY_UPDATE,
        ],
        organization_memberships=[
            {"organization_id": "bound-org", "role": "org_owner"},
            {"organization_id": "other-org", "role": "org_owner"},
        ],
        team_memberships=[
            {"team_id": "bound-team", "role": "team_admin"},
            {"team_id": "other-team", "role": "team_admin"},
        ],
        external_workspace=ExternalWorkspaceContext(
            integration_id="console",
            binding_id="binding",
            subject_id="subject",
            organization_id="bound-org",
            team_id="bound-team",
            inference_user_id="runtime",
            parent_id="parent",
            generation=1,
        ),
    )


def request(context: PlatformAuthContext | None, headers=()) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            app_config=SimpleNamespace(general_settings=SimpleNamespace(master_key="master-secret"))
        )
    )
    result = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/ui/api/keys",
            "headers": list(headers),
            "app": app,
        }
    )
    result.state.platform_auth = context
    result.state.master_session_status = MasterSessionStatus.ACTIVE
    return result


@pytest.mark.parametrize(
    "permission",
    [
        Permission.PLATFORM_ADMIN,
        Permission.KEY_UPDATE,
        Permission.SPEND_READ,
        Permission.TEAM_UPDATE,
        Permission.USER_READ,
        Permission.AUDIT_READ,
    ],
)
def test_every_scope_builder_obeys_customer_ceiling(permission):
    context = customer()
    assert not has_scoped_permission(context, permission)
    assert permission not in effective_permissions_for_context(context)
    scope = get_auth_scope(request(context), required_permission=permission)
    assert not scope.is_platform_admin and permission not in scope.effective_permissions
    assert not scope.granted_permissions and not scope.org_ids and not scope.team_ids


@pytest.mark.parametrize("permission", list(CUSTOMER_PERMISSION_CEILING))
def test_customer_permission_cannot_cross_registered_workspace(permission):
    context = customer()
    assert has_scoped_permission(context, permission, "bound-org", "bound-team")
    assert not has_scoped_permission(context, permission, "other-org", "bound-team")
    assert not has_scoped_permission(context, permission, "bound-org", "other-team")
    scope = get_auth_scope(request(context), required_permission=permission)
    assert set(scope.org_ids) <= {"bound-org"} and set(scope.team_ids) <= {"bound-team"}
    assert scope.external_workspace == context.external_workspace


class TeamLookup:
    def __init__(self):
        self.calls = []

    async def query_raw(self, query, *params):
        self.calls.append((query, params))
        return [{"team_id": params[0], "organization_id": "bound-org"}]


@pytest.mark.parametrize(
    "org_role,team_role",
    [
        ("org_owner", "team_developer"),
        ("org_member", "team_admin"),
    ],
)
async def test_team_write_helper_does_not_restore_customer_administrator_permissions(
    org_role, team_role
):
    context = customer().model_copy(
        update={
            "role": "org_user",
            "organization_memberships": [{"organization_id": "bound-org", "role": org_role}],
            "team_memberships": [{"team_id": "bound-team", "role": team_role}],
        }
    )
    incoming = request(context)
    database = TeamLookup()
    with pytest.raises(HTTPException) as denied:
        await _require_team_access(
            incoming, get_auth_scope(incoming), database, "bound-team", write=True
        )
    assert denied.value.status_code == 403 and database.calls == []


async def test_team_read_helper_does_not_disclose_foreign_team_existence():
    context = customer()
    incoming = request(context)
    database = TeamLookup()
    with pytest.raises(HTTPException) as denied:
        await _require_team_access(incoming, get_auth_scope(incoming), database, "other-team")
    assert denied.value.status_code == 404 and database.calls == []
    assert await _require_team_access(
        incoming, get_auth_scope(incoming), database, "bound-team"
    ) == {"team_id": "bound-team", "organization_id": "bound-org"}


@pytest.mark.asyncio
async def test_external_role_never_satisfies_platform_permission_or_master_session():
    incoming = request(customer())
    assert not has_platform_admin_session(incoming) and not has_master_key_session(incoming)
    with pytest.raises(HTTPException) as denied:
        await require_platform_permission(Permission.PLATFORM_ADMIN)(incoming)
    assert denied.value.status_code == 403


@pytest.mark.parametrize("context", [None, customer()])
@pytest.mark.parametrize("credential", ["header", "bearer", "cookie"])
@pytest.mark.asyncio
async def test_all_helpers_deny_mixed_master_credentials_even_for_expired_child(
    context, credential
):
    headers = [(b"cookie", b"deltallm_session=psk_ext1_expired")]
    authorization = None
    master = None
    if credential == "header":
        headers.append((b"x-master-key", b"master-secret"))
        master = "master-secret"
    elif credential == "bearer":
        headers.append((b"authorization", b"Bearer master-secret"))
        authorization = "Bearer master-secret"
    else:
        headers[0] = (
            b"cookie",
            b"deltallm_session=psk_ext1_expired; deltallm_master_session=master-cookie",
        )
        # Use the actual master cookie name to avoid assumptions in the test.
        from src.services.master_session_service import MASTER_SESSION_COOKIE_NAME

        headers[0] = (
            b"cookie",
            (
                "deltallm_session=psk_ext1_expired; "
                + MASTER_SESSION_COOKIE_NAME
                + "=master-cookie"
            ).encode(),
        )
    incoming = request(context, headers)
    for dependency in (
        require_authenticated,
        require_master_key,
        require_admin_permission(Permission.KEY_READ),
        require_any_admin_permission((Permission.KEY_READ,)),
    ):
        with pytest.raises(HTTPException) as denied:
            await dependency(incoming, authorization=authorization, x_master_key=master)
        assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        get_auth_scope(incoming, authorization=authorization, x_master_key=master)
    assert denied.value.status_code == 403


def test_external_ui_cannot_advertise_team_creation_or_shared_batch_work():
    context = customer()
    access = build_ui_access(
        authenticated=True,
        effective_permissions=effective_permissions_for_context(context),
        organization_memberships=context.organization_memberships,
        external_customer=True,
        spend_reporting_v2_enabled=True,
    )
    assert access["usage"] and access["keys"] and access["playground"]
    assert not access["team_create"] and not access["batches"] and not access["settings"]


def test_untrusted_peer_cannot_control_client_ip():
    resolver = ExternalClientResolver(ExternalAuthSettings(trusted_proxy_cidrs=("10.0.0.0/8",)))
    assert resolver.resolve("192.0.2.1", "garbage").address == "192.0.2.1"
    trusted = resolver.resolve("10.0.0.5", "203.0.113.9, 192.0.2.11, 10.0.0.4")
    assert trusted.address == "192.0.2.11" and trusted.forwarded
    with pytest.raises(ValueError):
        resolver.resolve("10.0.0.5", "garbage")
    with pytest.raises(ValueError):
        resolver.resolve("10.0.0.5", ",".join(["10.0.0.6"] * 17))
