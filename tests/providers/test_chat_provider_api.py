from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from src.config_runtime.secrets import SecretResolver
from src.db.named_credentials import NamedCredentialRecord
from src.db.repositories import ModelDeploymentRecord
from src.router.router import build_deployment_registry
from src.services.model_deployments import build_model_registry_from_records
from tests.test_named_credentials_api import _FakeNamedCredentialRepository
from tests.test_chat import _SpendRecorder
from tests.providers.test_chat_discovery import model_list, discovery_runtime


class _ReloadableNamedCredentialRepository(_FakeNamedCredentialRepository):
    async def list_by_ids(self, credential_ids: list[str]) -> dict[str, NamedCredentialRecord]:
        return {key: self.records[key] for key in credential_ids if key in self.records}


@pytest.fixture
def configured_app(test_app, contract):
    store = test_app.state.router.deployment_registry
    original = store["gpt-4o-mini"][0]
    deployment = replace(
        original,
        deltallm_params={
            "provider": contract["provider"],
            "model": contract["model"],
            "api_key": "provider-key",
        },
        model_info={
            "mode": "chat",
            "input_cost_per_token": 0.000001,
            "input_cost_per_token_cache_hit": 0.0000001,
            "output_cost_per_token": 0.000002,
        },
    )
    store.replace({**store.snapshot(), "gpt-4o-mini": [deployment]})
    return test_app


def request_body(*, stream=False):
    return {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Weather?"}],
        "stream": stream,
    }


async def test_named_credential_and_model_setup(client, test_app, contract, monkeypatch):
    test_app.state.settings.master_key = "mk-test"
    test_app.state.named_credential_repository = _ReloadableNamedCredentialRepository()
    monkeypatch.setenv("PROVIDER_EXPANSION_TEST_KEY", "private-test-secret")
    headers = {"Authorization": "Bearer mk-test"}
    presets = await client.get("/ui/api/provider-presets", headers=headers)
    assert any(item["provider"] == contract["provider"] for item in presets.json()["data"])
    created = await client.post(
        "/ui/api/named-credentials",
        headers=headers,
        json={
            "name": "Provider credential",
            "provider": contract["provider"],
            "connection_config": {
                "api_key": "os.environ/PROVIDER_EXPANSION_TEST_KEY",
                "api_base": contract["api_base"],
            },
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["connection_config"]["api_key"] == "***REDACTED***"
    assert "private-test-secret" not in created.text
    credential_id = created.json()["credential_id"]
    model = await client.post(
        "/ui/api/models",
        headers=headers,
        json={
            "model_name": "new-provider-chat",
            "named_credential_id": credential_id,
            "deltallm_params": {"provider": contract["provider"], "model": contract["model"]},
            "model_info": {"mode": "chat"},
        },
    )
    assert model.status_code == 200, model.text
    assert model.json()["provider"] == contract["provider"]
    assert "private-test-secret" not in model.text
    denied = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={**request_body(), "model": "new-provider-chat"},
    )
    assert denied.status_code in (403, 404)
    # The app fixture omits the hot-reload manager. Exercise its real registry
    # loading/secret-resolution step before issuing inference with the new model.
    loaded = await build_model_registry_from_records(
        [
            ModelDeploymentRecord(
                deployment_id=model.json()["deployment_id"],
                model_name="new-provider-chat",
                named_credential_id=credential_id,
                deltallm_params={"provider": contract["provider"], "model": contract["model"]},
                model_info={"mode": "chat"},
            )
        ],
        test_app.state.settings,
        named_credential_repository=test_app.state.named_credential_repository,
        secret_resolver=SecretResolver(),
    )
    test_app.state.router.deployment_registry.replace(
        build_deployment_registry(
            {
                **test_app.state.model_registry,
                **loaded,
            }
        )
    )
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer private-test-secret"
        assert str(request.url) == contract["api_base"] + "/chat/completions"
        return httpx.Response(200, json=contract["success"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        test_app.state.http_client = upstream
        allowed = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={**request_body(), "model": "new-provider-chat"},
        )
    assert allowed.status_code == 200, allowed.text
    assert len(calls) == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_chat_default_endpoint_auth_usage_and_single_upstream_call(
    client, configured_app, contract, stream
):
    captured = []
    responses = []
    recorder = _SpendRecorder()
    configured_app.state.spend_tracking_service = recorder

    def handle(request):
        captured.append(request)
        assert str(request.url) == contract["api_base"] + "/chat/completions"
        assert request.headers["Authorization"] == "Bearer provider-key"
        body = json.loads(request.content)
        assert body["model"] == contract["model"]
        assert "metadata" not in body
        if stream:
            if contract["provider"] != "zai":
                assert body["stream_options"]["include_usage"] is True
            response = httpx.Response(200, text="\n\n".join(contract["stream"]) + "\n\n")
        else:
            response = httpx.Response(200, json=contract["success"])
        responses.append(response)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        body = request_body(stream=stream)
        if stream:
            body["stream_options"] = {"include_usage": True}
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
            json=body,
        )
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    assert responses[0].is_closed
    await asyncio.wait_for(recorder.wait_for_events(1), timeout=3)
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event["usage"]["prompt_tokens_cached"] == 6
    assert event["cost"] == pytest.approx(0.0000126)
    assert event["metadata"]["provider"] == contract["provider"]
    if stream:
        assert "[DONE]" in response.text
        assert '"prompt_tokens_cached":6' in response.text.replace(" ", "")
        assert "reasoning_" in response.text
    else:
        assert response.json()["usage"]["prompt_tokens_cached"] == 6
        assert response.json()["usage"]["completion_tokens"] == 4


async def test_stream_failover_is_allowed_before_output(client, configured_app, contract):
    store = configured_app.state.router.deployment_registry
    primary = store["gpt-4o-mini"][0]
    fallback = replace(primary, deployment_id="provider-fallback")
    store.replace({**store.snapshot(), "gpt-4o-mini": [primary, fallback]})

    async def choose_primary(model_group, request_context):
        return primary

    configured_app.state.router.select_deployment = choose_primary
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "private provider body"}})
        return httpx.Response(200, text="\n\n".join(contract["stream"]) + "\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
            json=request_body(stream=True),
        )
    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert response.headers["x-deltallm-route-deployment"] == "provider-fallback"
    assert "private provider body" not in response.text


async def test_cancel_after_reasoning_closes_stream_and_releases_lease(
    client, configured_app, contract
):
    entered = asyncio.Event()
    closed = asyncio.Event()
    calls = []

    class BlockingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (contract["stream"][1] + "\n\n").encode()
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=BlockingBody())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        task = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
                json=request_body(stream=True),
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            deployment = configured_app.state.router.deployment_registry["gpt-4o-mini"][0]
            assert (
                await configured_app.state.router_state_backend.get_active_requests(
                    deployment.deployment_id
                )
                == 1
            )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed.is_set()
        assert len(calls) == 1
        assert (
            await configured_app.state.router_state_backend.get_active_requests(
                deployment.deployment_id
            )
            == 0
        )


async def test_provider_setup_requires_admin(client, test_app, contract):
    response = await client.post(
        "/ui/api/named-credentials",
        json={
            "name": "Unauthorized",
            "provider": contract["provider"],
            "connection_config": {"api_key": "private-test-key"},
        },
    )
    assert response.status_code in (401, 403)


async def test_discovery_and_health_use_control_client(client, configured_app, contract):
    configured_app.state.settings.master_key = "mk-test"
    calls = []

    def control_request(request):
        calls.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json=model_list(contract))

    def inference_request(request):
        raise AssertionError("Control-plane work used the inference HTTP pool")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(control_request)) as control,
        httpx.AsyncClient(transport=httpx.MockTransport(inference_request)) as inference,
    ):
        configured_app.state.control_http_client = control
        configured_app.state.provider_discovery_runtime = discovery_runtime(control._transport)
        configured_app.state.http_client = inference
        headers = {"Authorization": "Bearer mk-test"}
        discovery = await client.post(
            "/ui/api/provider-models/discover",
            headers=headers,
            json={
                "provider": contract["provider"],
                "api_key": "provider-key",
                "mode": "chat",
            },
        )
        assert discovery.status_code == 200, discovery.text
        deployment = configured_app.state.router.deployment_registry["gpt-4o-mini"][0]
        health = await client.post(
            f"/ui/api/models/{deployment.deployment_id}/health-check", headers=headers
        )
        assert health.status_code == 200, health.text
    assert len(calls) == (0 if contract["provider"] == "zai" else 2)


async def test_tool_conversation_preserves_provider_reasoning(client, configured_app, contract):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=contract["tool" if len(calls) == 1 else "success"])

    headers = {"Authorization": f"Bearer {configured_app.state._test_key}"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        first = await client.post("/v1/chat/completions", headers=headers, json=request_body())
        assert first.status_code == 200, first.text
        assistant = first.json()["choices"][0]["message"]
        tool = {
            "role": "tool",
            "tool_call_id": assistant["tool_calls"][0]["id"],
            "content": "Sunny, 20 C",
        }
        second = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                **request_body(),
                "messages": [*request_body()["messages"], assistant, tool],
            },
        )
    assert second.status_code == 200, second.text
    assert len(calls) == 2
    expected = contract["tool"]["choices"][0]["message"]
    for field in ("reasoning_content", "reasoning_details", "tool_calls"):
        if field in expected:
            assert calls[1]["messages"][1][field] == expected[field]


@pytest.mark.parametrize("frame_index", [1, 2], ids=["reasoning", "text"])
async def test_stream_error_after_output_never_retries(
    client, configured_app, contract, frame_index
):
    store = configured_app.state.router.deployment_registry
    primary = store["gpt-4o-mini"][0]
    fallback = replace(primary, deployment_id="unused-fallback")
    store.replace({**store.snapshot(), "gpt-4o-mini": [primary, fallback]})

    async def choose_primary(model_group, request_context):
        return primary

    configured_app.state.router.select_deployment = choose_primary
    closed = asyncio.Event()
    calls = []

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (contract["stream"][frame_index] + "\n\n").encode()
            raise httpx.ReadError("private upstream URL and payload")

        async def aclose(self):
            closed.set()

    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenBody())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        configured_app.state.http_client = upstream
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {configured_app.state._test_key}"},
            json=request_body(stream=True),
        )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert closed.is_set()
    assert "[DONE]" not in response.text
    assert "private upstream" not in response.text
    assert response.headers["x-deltallm-route-fallback-used"] == "false"
    for deployment in (primary, fallback):
        assert (
            await configured_app.state.router_state_backend.get_active_requests(
                deployment.deployment_id
            )
            == 0
        )
