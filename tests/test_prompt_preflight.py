from __future__ import annotations

import asyncio
import httpx
import pytest

from src.db.prompt_registry import PromptBindingRecord, PromptResolvedRecord
from src.models.responses import UserAPIKeyAuth
from src.services.prompt_registry import PromptProvenance, PromptRegistryService, PromptRenderOutput
from src.services.runtime_scopes import annotate_auth_metadata, resolve_runtime_scope_context


class _InjectingPromptService:
    async def resolve_and_render(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return PromptRenderOutput(
            messages=[{"role": "system", "content": "You must answer as DeltaLLM assistant."}],
            provenance=PromptProvenance(source="binding", template_key="support.prompt", version=3, label="production"),
            rendered_prompt={"messages": [{"role": "system", "content": "You must answer as DeltaLLM assistant."}]},
        )


class _FailingPromptService:
    async def resolve_and_render(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        raise ValueError("variables.customer_tier is required")


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def log_spend(self, **kwargs):  # noqa: ANN003, ANN201
        self.events.append(kwargs)


class _PromptRepoForDefaults:
    async def resolve_prompt(self, *, template_key: str, label: str | None = None, version: int | None = None):  # noqa: ANN201
        del version
        if template_key == "support.prompt":
            return PromptResolvedRecord(
                prompt_template_id="tmpl-support",
                template_key="support.prompt",
                prompt_version_id="ver-support",
                version=3,
                status="published",
                label=label or "production",
                template_body={"text": "Support prompt active."},
                variables_schema=None,
                model_hints=None,
                route_preferences=None,
            )
        if template_key == "key.prompt":
            return PromptResolvedRecord(
                prompt_template_id="tmpl-key",
                template_key="key.prompt",
                prompt_version_id="ver-key",
                version=2,
                status="published",
                label=label or "production",
                template_body={"text": "Key prompt wins."},
                variables_schema=None,
                model_hints=None,
            )
        return None

    async def resolve_binding(self, *, scope_type: str, scope_id: str):  # noqa: ANN201
        if scope_type == "key" and scope_id == "sk-test":
            return PromptBindingRecord(
                prompt_binding_id="binding-key",
                scope_type="key",
                scope_id=scope_id,
                prompt_template_id="tmpl-key",
                template_key="key.prompt",
                label="production",
                priority=1,
                enabled=True,
                metadata=None,
                created_at=None,
                updated_at=None,
            )
        return None

    async def create_render_log(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return None


class _RouteGroupPromptRepo:
    async def get_default_prompt(self, group_key: str):  # noqa: ANN201
        if group_key == "gpt-4o-mini":
            return {"template_key": "support.prompt", "label": "production"}
        return None


class _PromptRepoWithoutBindings(_PromptRepoForDefaults):
    async def resolve_binding(self, *, scope_type: str, scope_id: str):  # noqa: ANN201
        del scope_type, scope_id
        return None


class _PromptRepoWithScopedBindings(_PromptRepoForDefaults):
    async def resolve_binding(self, *, scope_type: str, scope_id: str):  # noqa: ANN201
        if scope_type == "user" and scope_id == "user-1":
            return PromptBindingRecord(
                prompt_binding_id="binding-user",
                scope_type="user",
                scope_id=scope_id,
                prompt_template_id="tmpl-support",
                template_key="support.prompt",
                label="production",
                priority=1,
                enabled=True,
                metadata=None,
                created_at=None,
                updated_at=None,
            )
        if scope_type == "key" and scope_id == "sk-test":
            return PromptBindingRecord(
                prompt_binding_id="binding-key",
                scope_type="key",
                scope_id=scope_id,
                prompt_template_id="tmpl-key",
                template_key="key.prompt",
                label="production",
                priority=1,
                enabled=True,
                metadata=None,
                created_at=None,
                updated_at=None,
            )
        return None


@pytest.mark.asyncio
async def test_chat_preflight_injects_prompt_messages(client, test_app):
    captured: dict[str, object] = {}

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        captured["json"] = json
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post
    test_app.state.prompt_registry_service = _InjectingPromptService()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    request_payload = captured.get("json")
    assert isinstance(request_payload, dict)
    assert request_payload["messages"][0]["role"] == "system"
    assert request_payload["messages"][0]["content"] == "You must answer as DeltaLLM assistant."


@pytest.mark.asyncio
async def test_chat_preflight_keeps_spend_metadata_with_unified_resolution_block(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.prompt_registry_service = _InjectingPromptService()

    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-prompt-resolution"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.events
    metadata = (recorder.events[-1].get("metadata") or {})
    assert metadata.get("routing_decision")
    assert metadata.get("prompt_provenance", {}).get("template_key") == "support.prompt"
    resolution = metadata.get("request_resolution") or {}
    assert resolution.get("routing", {}).get("model_group") == "gpt-4o-mini"
    assert resolution.get("prompt", {}).get("template_key") == "support.prompt"


@pytest.mark.asyncio
async def test_chat_preflight_returns_400_for_prompt_resolution_errors(client, test_app):
    test_app.state.prompt_registry_service = _FailingPromptService()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 400
    payload = response.json()["error"]
    assert payload["type"] == "invalid_request_error"
    assert "prompt resolution failed" in payload["message"]


@pytest.mark.asyncio
async def test_chat_preflight_uses_route_group_default_prompt_when_no_higher_scope_binding(client, test_app):
    captured: dict[str, object] = {}

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        captured["json"] = json
        payload = {
            "id": "chatcmpl-default",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post
    test_app.state.prompt_registry_service = PromptRegistryService(
        repository=_PromptRepoWithoutBindings(),
        route_group_repository=_RouteGroupPromptRepo(),
    )

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    request_payload = captured.get("json")
    assert isinstance(request_payload, dict)
    assert request_payload["messages"][0]["content"] == "Support prompt active."


@pytest.mark.asyncio
async def test_chat_preflight_keeps_explicit_prompt_precedence_over_route_group_default(client, test_app):
    captured: dict[str, object] = {}

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        captured["json"] = json
        payload = {
            "id": "chatcmpl-key",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    class _PromptRepoWithExplicit(_PromptRepoWithoutBindings):
        async def resolve_prompt(self, *, template_key: str, label: str | None = None, version: int | None = None):  # noqa: ANN201
            if template_key == "key.prompt":
                return PromptResolvedRecord(
                    prompt_template_id="tmpl-key",
                    template_key="key.prompt",
                    prompt_version_id="ver-key",
                    version=2,
                    status="published",
                    label=label or "production",
                    template_body={"text": "Key prompt wins."},
                    variables_schema=None,
                    model_hints=None,
                )
            return await super().resolve_prompt(template_key=template_key, label=label, version=version)

    test_app.state.prompt_registry_service = PromptRegistryService(
        repository=_PromptRepoWithExplicit(),
        route_group_repository=_RouteGroupPromptRepo(),
    )

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "metadata": {"prompt_ref": {"key": "key.prompt", "label": "production"}},
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    request_payload = captured.get("json")
    assert isinstance(request_payload, dict)
    assert request_payload["messages"][0]["content"] == "Key prompt wins."


@pytest.mark.asyncio
async def test_prompt_registry_resolves_legacy_key_binding_from_api_key_scope() -> None:
    service = PromptRegistryService(repository=_PromptRepoForDefaults(), route_group_repository=_RouteGroupPromptRepo())

    resolved = await service.resolve_and_render(
        explicit_reference=None,
        variables={},
        api_key="sk-test",
        user_id=None,
        team_id=None,
        organization_id=None,
        route_group_key=None,
        model="gpt-4o-mini",
        request_id="req-1",
        scope_context=resolve_runtime_scope_context(
            annotate_auth_metadata(
                UserAPIKeyAuth(api_key="sk-test"),
                auth_source="api_key",
                api_key_scope_id="sk-test",
            )
        ),
    )

    assert resolved is not None
    assert resolved.provenance.binding_scope == "api_key"
    assert resolved.provenance.binding_scope_id == "sk-test"
    assert resolved.messages[0]["content"] == "Key prompt wins."


@pytest.mark.asyncio
async def test_prompt_registry_user_binding_precedes_api_key_binding() -> None:
    service = PromptRegistryService(repository=_PromptRepoWithScopedBindings())
    auth = annotate_auth_metadata(
        UserAPIKeyAuth(api_key="sk-test", user_id="user-1"),
        auth_source="api_key",
        api_key_scope_id="sk-test",
    )

    resolved = await service.resolve_and_render(
        explicit_reference=None,
        variables={},
        api_key=auth.api_key,
        user_id=auth.user_id,
        team_id=auth.team_id,
        organization_id=auth.organization_id,
        route_group_key=None,
        model="gpt-4o-mini",
        request_id="req-2",
        scope_context=resolve_runtime_scope_context(auth),
    )

    assert resolved is not None
    assert resolved.provenance.binding_scope == "user"
    assert resolved.provenance.binding_scope_id == "user-1"
    assert resolved.messages[0]["content"] == "Support prompt active."


@pytest.mark.asyncio
async def test_chat_preflight_applies_prompt_route_preference_tags(client, test_app):
    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        payload = {
            "id": "chatcmpl-route-tags",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post
    class _PromptRepoWithRoutePreferences(_PromptRepoWithoutBindings):
        async def resolve_prompt(self, *, template_key: str, label: str | None = None, version: int | None = None):  # noqa: ANN201
            resolved = await super().resolve_prompt(template_key=template_key, label=label, version=version)
            if resolved is None or template_key != "support.prompt":
                return resolved
            return PromptResolvedRecord(
                prompt_template_id=resolved.prompt_template_id,
                template_key=resolved.template_key,
                prompt_version_id=resolved.prompt_version_id,
                version=resolved.version,
                status=resolved.status,
                label=resolved.label,
                template_body=resolved.template_body,
                variables_schema=resolved.variables_schema,
                model_hints=resolved.model_hints,
                route_preferences={"tags": ["support", "vip"]},
            )

    test_app.state.prompt_registry_service = PromptRegistryService(
        repository=_PromptRepoWithRoutePreferences(),
        route_group_repository=_RouteGroupPromptRepo(),
    )
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    for deployment in test_app.state.router.deployment_registry.get("gpt-4o-mini", []):
        deployment.tags = ["existing", "support", "vip"]

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "metadata": {"tags": ["existing"]},
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.events
    metadata = (recorder.events[-1].get("metadata") or {})
    assert metadata.get("prompt_provenance", {}).get("route_preferences") == {"tags": ["support", "vip"]}
    assert metadata.get("request_resolution", {}).get("prompt", {}).get("route_preferences") == {"tags": ["support", "vip"]}


@pytest.mark.asyncio
async def test_prompt_registry_dry_run_returns_rendered_prompt_and_provenance():
    service = PromptRegistryService(repository=_PromptRepoForDefaults())

    result = await service.dry_run_render(
        template_key="support.prompt",
        label="production",
        version=None,
        variables={},
    )

    assert result["template_key"] == "support.prompt"
    assert result["version"] == 3
    assert result["messages"][0]["content"] == "Support prompt active."
    assert result["provenance"]["source"] == "dry_run"
    assert result["provenance"]["template_key"] == "support.prompt"
    assert result["provenance"]["version"] == 3
    assert result["provenance"]["label"] == "production"
    assert result["cache_tier"] == "db"
