from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.mcp.orchestrator import MCPChatOrchestrator
from src.models.platform_auth import PlatformAuthContext
from src.models.errors import ServiceUnavailableError
from src.models.requests import ChatCompletionRequest, FunctionToolDefinition, ToolChoice
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.profiled_chat import ProfiledChatAdapter
from src.providers.healthcheck import HealthProbeResult
from src.services.master_session_service import MASTER_SESSION_COOKIE_NAME, MasterSessionStatus


@pytest.mark.parametrize("role", [None, "org_user", "org_admin", "team_admin", "platform_admin"])
@pytest.mark.parametrize("identifier", ["existing", "nonexistent"])
@pytest.mark.parametrize("base", ["https://public.example", "https://10.0.0.1"])
async def test_operator_actions_deny_before_lookup(
    client, test_app, monkeypatch, role, identifier, base
):
    context = (
        None
        if role is None
        else PlatformAuthContext(
            account_id="test-account",
            email="test@example.com",
            role=role,
            organization_memberships=[{"organization_id": "org-1", "role": "org_admin"}],
            team_memberships=[{"team_id": "team-1", "role": "team_admin"}],
            mfa_enabled=role == "platform_admin",
            mfa_verified=False,
        )
    )
    test_app.state.platform_identity_service = SimpleNamespace(
        get_context_for_session=AsyncMock(return_value=context)
    )
    lookup = AsyncMock(side_effect=AssertionError("credential lookup before authorization"))
    deployment_lookup = Mock(side_effect=AssertionError("deployment lookup before authorization"))
    monkeypatch.setattr("src.api.admin.endpoints.models._load_named_credential_or_400", lookup)
    monkeypatch.setattr(
        "src.api.admin.endpoints.models._find_runtime_deployment", deployment_lookup
    )
    for path, body in [
        (
            "/ui/api/provider-models/discover",
            {"provider": "deepseek", "api_base": base, "named_credential_id": identifier},
        ),
        (f"/ui/api/models/{identifier}/health-check", {}),
    ]:
        response = await client.post(path, json=body, cookies={"deltallm_session": "session-token"})
        assert response.status_code == (401 if role is None else 403)
    lookup.assert_not_called()
    deployment_lookup.assert_not_called()


@pytest.mark.parametrize("auth_mode", ["bearer", "header", "master_session", "platform_session"])
@pytest.mark.parametrize("background", [False, True])
async def test_authorized_operator_actions_preserve_supported_auth(
    client, test_app, monkeypatch, auth_mode, background
):
    test_app.state.settings.master_key = "mk-test"
    headers = {}
    cookies = {}
    if auth_mode == "bearer":
        headers = {"Authorization": "Bearer mk-test"}
    elif auth_mode == "header":
        headers = {"X-Master-Key": "mk-test"}
    elif auth_mode == "master_session":
        cookies = {MASTER_SESSION_COOKIE_NAME: "master-token"}
        test_app.state.master_session_service = SimpleNamespace(
            validate_session=AsyncMock(return_value=MasterSessionStatus.ACTIVE)
        )
    else:
        cookies = {"deltallm_session": "platform-token"}
        test_app.state.platform_identity_service = SimpleNamespace(
            get_context_for_session=AsyncMock(
                return_value=PlatformAuthContext(
                    account_id="admin",
                    email="admin@example.com",
                    role="platform_admin",
                    mfa_enabled=True,
                    mfa_verified=True,
                )
            )
        )
    probe = AsyncMock(return_value=HealthProbeResult(healthy=True))
    if background:
        test_app.state.background_health_checker = SimpleNamespace(check_deployment_once=probe)
    else:
        monkeypatch.setattr("src.api.admin.endpoints.models.probe_provider_health", probe)
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    discovery = await client.post(
        "/ui/api/provider-models/discover",
        json={"provider": "zai"},
        headers=headers,
        cookies=cookies,
    )
    health = await client.post(
        f"/ui/api/models/{deployment.deployment_id}/health-check", headers=headers, cookies=cookies
    )
    assert discovery.status_code == health.status_code == 200
    probe.assert_awaited_once()


async def test_defaulted_tool_discriminators_survive_omission(contract):
    schema = {"type": "object", "properties": {"value": {"type": "string", "default": None}}}
    payload = ChatCompletionRequest(
        model="alias",
        messages=[{"role": "user", "content": "Search"}],
        tools=[FunctionToolDefinition(function={"name": "search", "parameters": schema})],
        tool_choice=ToolChoice(function={"name": "search"}),
    )
    async with httpx.AsyncClient() as client:
        result = await ProfiledChatAdapter(
            client, CHAT_PROVIDER_PROFILES[contract["provider"]]
        ).translate_request(payload, {"model": contract["model"], "provider": contract["provider"]})
    assert result["tools"][0]["type"] == "function"
    assert result["tool_choice"]["type"] == "function"
    assert result["tools"][0]["function"]["parameters"] == schema
    assert "temperature" not in result
    assert "top_p" not in result


@pytest.mark.parametrize(
    "reasoning",
    [
        {},
        {"reasoning_content": None},
        {"reasoning_content": "private thought"},
        {
            "reasoning_details": [
                {"type": "reasoning.encrypted", "data": {"signature": "opaque", "value": None}}
            ]
        },
    ],
)
def test_mcp_assistant_history_preserves_reasoning_presence(reasoning):
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call-1", "type": "function", "function": {"name": "search", "arguments": "{}"}}
        ],
        **reasoning,
    }
    result = MCPChatOrchestrator._assistant_message_from_response(
        {"choices": [{"message": message}]}
    ).model_dump(mode="json", exclude_unset=True)
    assert result["content"] is None
    for field in ("reasoning_content", "reasoning_details"):
        assert (field in result) == (field in reasoning)
        if field in reasoning:
            assert result[field] == reasoning[field]


def test_invalid_reasoning_history_raises_safe_error_before_tools():
    import traceback

    private_material = "private-provider-material"
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "ok",
                    "reasoning_details": private_material,
                }
            }
        ]
    }
    with pytest.raises(ServiceUnavailableError) as error:
        MCPChatOrchestrator._assistant_message_from_response(payload)
    assert "private-provider-material" not in "".join(traceback.format_exception(error.value))
