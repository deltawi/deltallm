from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

from src.auth.external_client import ExternalClientResolver
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_policy import CUSTOMER_PERMISSION_CEILING
from src.services.access.creator_mcp_access import CreatorMCPAccessService
from tests.test_external_customer_scope import customer
from tests.test_ui_mcp import (
    _FakeBindingPolicyListQueryClient,
    _FakeManagedAssetAccessRepository,
    _FakeMCPRegistryService,
    _FakeMCPRepository,
    _IdentityService,
)


class PrivateMCPQueryClient(_FakeBindingPolicyListQueryClient):
    def __init__(self, repository: _FakeMCPRepository) -> None:
        super().__init__()
        self.repository = repository

    async def query_raw(self, query, *params):
        if "FROM deltallm_mcpserver s" in query:
            if "COUNT(*)" in query:
                return [{"total": len(self.repository.servers)}]
            return [asdict(server) for server in self.repository.servers.values()]
        return await super().query_raw(query, *params)


async def test_customer_private_mcp_access_survives_required_permission_denial(test_app, client):
    repository = _FakeMCPRepository()
    access = _FakeManagedAssetAccessRepository(test_app)
    query_client = PrivateMCPQueryClient(repository)
    query_client.bindings = []
    query_client.policies = []
    test_app.state.mcp_repository = repository
    test_app.state.managed_asset_access_repository = access
    test_app.state.creator_mcp_access_service = CreatorMCPAccessService(access, repository)
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(repository)
    test_app.state.prisma_manager = SimpleNamespace(client=query_client)
    test_app.state.platform_identity_service = _IdentityService(
        customer().model_copy(
            update={"role": "org_user", "permissions": list(CUSTOMER_PERMISSION_CEILING)}
        )
    )
    test_app.state.external_auth_runtime = SimpleNamespace(
        client_resolver=ExternalClientResolver(
            ExternalAuthSettings(allowed_origins=("https://console.example.com",))
        )
    )
    client.cookies.set("deltallm_session", "mcp-asset-session")
    client.headers["Origin"] = "https://console.example.com"
    created = await client.post(
        "/ui/api/mcp-servers",
        json={
            "server_key": "private-docs",
            "name": "Private Docs",
            "base_url": "https://mcp.example.com",
            "access": {"visibility": "private"},
        },
    )
    assert created.status_code == 200, created.text
    server_id = created.json()["mcp_server_id"]
    listing = await client.get("/ui/api/mcp-servers")
    detail = await client.get(f"/ui/api/mcp-servers/{server_id}")
    assert listing.status_code == detail.status_code == 200
    assert server_id in listing.text
    assert detail.json()["server"]["access"]["effective_role"] == "owner"
    updated = await client.patch(
        f"/ui/api/mcp-servers/{server_id}", json={"name": "Updated private docs"}
    )
    assert updated.status_code == 200, updated.text
    deleted = await client.delete(f"/ui/api/mcp-servers/{server_id}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
