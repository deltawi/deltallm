from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.middleware.rate_limit import _check_and_acquire_rate_limits
from src.models.errors import InvalidRequestError
from src.services.limit_counter import LimitCounter


async def _multimodal_success_response(url: str, *args, **kwargs):  # noqa: ANN001, ANN202
    del args, kwargs
    request = httpx.Request("POST", url)
    if url.endswith("/images/generations"):
        return httpx.Response(
            200,
            json={"created": 1, "data": [{"url": "https://example.test/image.png"}]},
            request=request,
        )
    if url.endswith("/rerank"):
        return httpx.Response(
            200,
            json={"results": [], "usage": {"prompt_tokens": 1, "total_tokens": 1}},
            request=request,
        )
    if url.endswith("/audio/speech"):
        return httpx.Response(
            200,
            content=b"audio",
            headers={"content-type": "audio/mpeg"},
            request=request,
        )
    if url.endswith("/audio/transcriptions"):
        return httpx.Response(200, json={"text": "hello"}, request=request)
    return httpx.Response(404, json={"error": "not found"}, request=request)


def _multimodal_request(path: str) -> dict[str, object]:
    if path == "/v1/images/generations":
        return {"json": {"model": "gpt-4o-mini", "prompt": "cat"}}
    if path == "/v1/rerank":
        return {"json": {"model": "gpt-4o-mini", "query": "q", "documents": ["a"]}}
    if path == "/v1/audio/speech":
        return {"json": {"model": "gpt-4o-mini", "input": "hello", "voice": "alloy"}}
    return {
        "files": {"file": ("audio.wav", b"abc", "audio/wav")},
        "data": {"model": "gpt-4o-mini", "response_format": "json"},
    }


@pytest.mark.asyncio
async def test_rate_limit_rpm_enforced(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
    }

    r1 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r2 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r3 = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    assert r3.headers.get("Retry-After") is not None


@pytest.mark.asyncio
async def test_rate_limit_org_rpm_enforced_before_key_limit(client, test_app):
    from src.db.callable_targets import CallableTargetBindingRecord

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
    }
    record = next(iter(test_app.state._test_repo.records.values()))
    record.rpm_limit = 50
    record.org_rpm_limit = 1
    record.organization_id = "org-test"

    # Add callable-target binding for org-test to allow model access
    test_app.state.callable_target_grant_service.repository.bindings.append(
        CallableTargetBindingRecord(
            callable_target_binding_id="ctb-test-1",
            callable_key="gpt-4o-mini",
            scope_type="organization",
            scope_id="org-test",
            enabled=True,
        )
    )
    await test_app.state.callable_target_grant_service.reload()

    ok = await client.post("/v1/chat/completions", headers=headers, json=body)
    blocked = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert ok.status_code == 200
    assert blocked.status_code == 429
    payload = blocked.json()
    assert payload["error"]["code"] == "org_rpm_exceeded"
    assert payload["error"]["param"] == "org_rpm"


@pytest.mark.asyncio
async def test_rate_limit_user_tpm_enforced(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    record = next(iter(test_app.state._test_repo.records.values()))
    record.rpm_limit = 50
    record.tpm_limit = 10000
    record.user_id = "user-test"
    record.user_tpm_limit = 5

    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "x" * 200}],
    }
    blocked = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert blocked.status_code == 429
    payload = blocked.json()
    assert payload["error"]["code"] == "user_tpm_exceeded"
    assert payload["error"]["param"] == "user_tpm"


@pytest.mark.asyncio
async def test_audio_transcription_team_model_rpm_enforced_for_multipart_requests(client, test_app):
    class RecordingLimitCounter(LimitCounter):
        def __init__(self) -> None:
            super().__init__(redis_client=None)
            self.seen_checks = []

        async def check_rate_limits_atomic(self, checks):
            self.seen_checks.append(list(checks))
            return await super().check_rate_limits_atomic(checks)

        async def check_rate_limits_and_tier_fair_share_atomic(
            self,
            rate_checks,
            fair_share_checks,
            **kwargs,
        ):
            self.seen_checks.append(list(rate_checks))
            return await super().check_rate_limits_and_tier_fair_share_atomic(
                rate_checks,
                fair_share_checks,
                **kwargs,
            )

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    record = next(iter(test_app.state._test_repo.records.values()))
    record.rpm_limit = 50
    record.team_model_rpm_limit = {"gpt-4o-mini": 1}
    test_app.state.limit_counter = RecordingLimitCounter()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info["mode"] = "audio_transcription"

    async def stt_post(url: str, headers: dict[str, str], files, data, timeout: int):  # noqa: ANN001, ANN201
        del headers, files, data, timeout
        if url.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello"}, request=httpx.Request("POST", url))
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = stt_post

    files = {"file": ("audio.wav", b"abc", "audio/wav")}
    data = {"model": "gpt-4o-mini", "response_format": "json"}

    ok = await client.post("/v1/audio/transcriptions", headers=headers, files=files, data=data)
    blocked = await client.post("/v1/audio/transcriptions", headers=headers, files=files, data=data)

    assert ok.status_code == 200
    assert blocked.status_code == 429
    scopes = {check.scope for check in test_app.state.limit_counter.seen_checks[0]}
    assert "team_model_rpm" in scopes
    payload = blocked.json()
    assert payload["error"]["code"] == "team_model_rpm_exceeded"
    assert payload["error"]["param"] == "team_model_rpm"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "routing_mode"),
    [
        ("/v1/images/generations", "image_generation"),
        ("/v1/rerank", "rerank"),
        ("/v1/audio/speech", "audio_speech"),
        ("/v1/audio/transcriptions", "audio_transcription"),
    ],
)
async def test_multimodal_access_denial_does_not_consume_rate_quota(
    client,
    test_app,
    path: str,
    routing_mode: str,
) -> None:
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    record = next(iter(test_app.state._test_repo.records.values()))
    record.rpm_limit = 1
    test_app.state.http_client.post = _multimodal_success_response
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info["mode"] = routing_mode
    if routing_mode == "rerank":
        deployment.deltallm_params["provider"] = "vllm"

    grant_service = test_app.state.callable_target_grant_service
    grant_repository = grant_service.repository
    removed_bindings = [
        binding for binding in grant_repository.bindings if binding.callable_key == "gpt-4o-mini"
    ]
    grant_repository.bindings = [
        binding for binding in grant_repository.bindings if binding.callable_key != "gpt-4o-mini"
    ]
    await grant_service.reload()

    denied = await client.post(path, headers=headers, **_multimodal_request(path))

    assert denied.status_code == 403
    assert not [key for key in test_app.state.redis.store if key.startswith("ratelimit:")]

    grant_repository.bindings.extend(removed_bindings)
    await grant_service.reload()

    allowed = await client.post(path, headers=headers, **_multimodal_request(path))

    assert allowed.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "request_kwargs"),
    [
        ("/v1/images/generations", {"json": {"model": "gpt-4o-mini"}}),
        ("/v1/rerank", {"json": {"model": "gpt-4o-mini", "query": "q"}}),
        ("/v1/audio/speech", {"json": {"model": "gpt-4o-mini", "voice": "alloy"}}),
        (
            "/v1/audio/transcriptions",
            {"data": {"model": "gpt-4o-mini", "response_format": "json"}},
        ),
    ],
)
async def test_invalid_multimodal_request_does_not_consume_rate_quota(
    client,
    test_app,
    path: str,
    request_kwargs: dict[str, object],
) -> None:
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(path, headers=headers, **request_kwargs)

    assert response.status_code == 422
    assert not [key for key in test_app.state.redis.store if key.startswith("ratelimit:")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (b"{", -32700),
        (b'[{"jsonrpc":"2.0"}]', -32600),
        (b'{"jsonrpc":"2.0","id":1}', -32600),
    ],
)
async def test_invalid_mcp_envelope_does_not_consume_rate_quota(
    client,
    test_app,
    content: bytes,
    expected_code: int,
) -> None:
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "Content-Type": "application/json",
    }
    response = await client.post("/mcp", headers=headers, content=content)

    assert response.status_code == 200
    assert response.json()["error"]["code"] == expected_code
    assert not [key for key in test_app.state.redis.store if key.startswith("ratelimit:")]


@pytest.mark.asyncio
async def test_rate_limit_rejects_unreadable_request_body():
    class RaisingRequest:
        def __init__(self) -> None:
            self.state = SimpleNamespace(
                _rate_limit_checked=False,
                user_api_key=SimpleNamespace(
                    key_rpm_limit=None,
                    key_tpm_limit=None,
                    rpm_limit=10,
                    tpm_limit=1000,
                    organization_id=None,
                    org_rpm_limit=None,
                    org_tpm_limit=None,
                    team_id=None,
                    team_rpm_limit=None,
                    team_tpm_limit=None,
                    user_id=None,
                    user_rpm_limit=None,
                    user_tpm_limit=None,
                    api_key="sk-test",
                    max_parallel_requests=5,
                ),
            )
            self.app = SimpleNamespace(state=SimpleNamespace(limit_counter=SimpleNamespace()))

        async def body(self) -> bytes:
            raise RuntimeError("body stream failed")

    request = RaisingRequest()
    with pytest.raises(InvalidRequestError):
        await _check_and_acquire_rate_limits(request)  # type: ignore[arg-type]
