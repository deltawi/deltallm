from copy import deepcopy
import asyncio
import json

import httpx
import pytest

from src.db.callable_targets import CallableTargetBindingRecord
from src.router.failover import FallbackConfig
from src.router.runtime_generation import with_authorization_snapshot
from tests.router.selection.provider_fixtures import response_body
from tests.router.selection.test_realtime import configure
from tests.test_routing_cache_identity import _publish

pytestmark = pytest.mark.app


async def configure_fallback(
    test_app, upstream, *, authorize=True, second=False, fallback_field="fallbacks"
):
    billing, selected = configure(test_app, upstream)
    selected["key"] = "selected"
    entries = test_app.state.model_registry["backing"]
    plain = deepcopy(entries[0])
    plain["deployment_id"] = "plain"
    plain["deltallm_params"]["model"] = "openai/plain"
    entries.append(plain)
    groups = [
        {"key": "gpt-4o-mini", "mode": "chat", "members": [{"deployment_id": "plain"}]},
        selected,
    ]
    targets = ["selected"]
    if second:
        later = deepcopy(selected)
        later["key"] = "later"
        later["selector"]["classifier_deployment_id"] = "classifier-2"
        later["selector"]["lanes"][0]["id"] = "silver"
        later["selector"]["lanes"][1]["id"] = "gold"
        later["members"] = [
            {"deployment_id": "classifier-2", "lane": "silver"},
            {"deployment_id": "quality-2", "lane": "gold"},
        ]
        for source in entries[:2]:
            extra = deepcopy(source)
            extra["deployment_id"] += "-2"
            extra["deltallm_params"]["model"] += "-2"
            entries.append(extra)
        groups.append(later)
        targets.append("later")
    if authorize:
        grants = test_app.state.callable_target_grant_service
        grants.repository.bindings.extend(
            CallableTargetBindingRecord(
                callable_target_binding_id=f"binding-{key}",
                callable_key=key,
                scope_type="organization",
                scope_id="org-default",
                enabled=True,
            )
            for key in targets
        )
        await grants.reload()
    runtime = _publish(
        test_app,
        groups,
        failover_config=FallbackConfig(**{fallback_field: {"gpt-4o-mini": targets}}, backoff_max=0),
    )
    runtime = with_authorization_snapshot(
        runtime, test_app.state.callable_target_grant_service.snapshot()
    )
    test_app.state.routing_runtime_generation_store.replace(runtime)
    return billing


@pytest.mark.parametrize(
    "field,code",
    [
        ("context_window_fallbacks", "context_length_exceeded"),
        ("content_policy_fallbacks", "content_filter"),
    ],
)
async def test_classified_fallback_lazily_executes_one_selector(client, test_app, field, code):
    calls = []

    def handle(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "plain":
            return httpx.Response(400, json={"error": {"message": code, "code": code}})
        return httpx.Response(
            200,
            json=response_body(text='{"lane":"quality"}' if model == "classifier" else "answer"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing = await configure_fallback(test_app, upstream, fallback_field=field)
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200, response.text
        assert calls == ["plain", "classifier", "quality"]
        billing.reserve.assert_awaited_once()


@pytest.mark.parametrize("fail_primary", [False, True])
async def test_first_selector_is_lazy_on_ordinary_fallback(client, test_app, fail_primary):
    calls = []

    def handle(request):
        data = json.loads(request.content)
        calls.append(data["model"])
        if data["model"] == "plain" and fail_primary:
            return httpx.Response(503, json={"error": {"message": "mock unavailable"}})
        return httpx.Response(
            200,
            json=response_body(
                text='{"lane":"quality"}' if data["model"] == "classifier" else "answer"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing = await configure_fallback(test_app, upstream)
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200, response.text
        assert calls == (["plain", "classifier", "quality"] if fail_primary else ["plain"])
        assert billing.reserve.await_count == int(fail_primary)
        assert billing.accept_selector.await_count == int(fail_primary)


async def test_later_selector_fallback_reuses_minimum_rank_with_different_lane_names(
    client, test_app
):
    calls = []

    def handle(request):
        data = json.loads(request.content)
        calls.append(data["model"])
        if data["model"] in {"plain", "quality"}:
            return httpx.Response(503, json={"error": {"message": "mock unavailable"}})
        return httpx.Response(
            200,
            json=response_body(
                text='{"lane":"quality"}' if data["model"] == "classifier" else "answer"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing = await configure_fallback(test_app, upstream, second=True)
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200, response.text
        assert calls == ["plain", "classifier", "quality", "quality-2"]
        billing.reserve.assert_awaited_once()
        billing.accept_selector.assert_awaited_once()


async def test_local_context_rejection_reaches_selector_without_dispatching_primary(
    client, test_app
):
    calls = []

    def handle(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        return httpx.Response(
            200,
            json=response_body(text='{"lane":"quality"}' if model == "classifier" else "answer"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing = await configure_fallback(
            test_app, upstream, fallback_field="context_window_fallbacks"
        )
        current = test_app.state.routing_runtime_generation_store.require_snapshot()
        groups = deepcopy(list(current.route_groups))
        groups[0]["context"] = {"mode": "eligible-only", "unknown_capacity": "exclude"}
        test_app.state.model_registry["backing"][-1]["model_info"]["max_tokens"] = 128
        _publish(test_app, groups, failover_config=current.failover_config)
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200, response.text
        assert calls == ["classifier", "quality"]
        billing.reserve.assert_awaited_once()


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/responses"])
async def test_local_context_fallback_planning_obeys_original_deadline(client, test_app, endpoint):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=response_body(text='{"lane":"quality"}'))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing = await configure_fallback(
            test_app, upstream, fallback_field="context_window_fallbacks"
        )
        current = test_app.state.routing_runtime_generation_store.require_snapshot()
        groups = deepcopy(list(current.route_groups))
        groups[0]["context"] = {"mode": "eligible-only", "unknown_capacity": "exclude"}
        groups[0]["timeouts"] = {"global_seconds": 0.1}
        test_app.state.model_registry["backing"][-1]["model_info"]["max_tokens"] = 128
        runtime = _publish(test_app, groups, failover_config=current.failover_config)
        original = runtime.router.plan_deployments

        async def blocked_planning(group_keys, context):
            if list(group_keys) == ["selected"]:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return await original(group_keys, context)

        runtime.router.plan_deployments = blocked_planning
        body = {"model": "gpt-4o-mini"}
        if endpoint.endswith("completions"):
            body["messages"] = [{"role": "user", "content": "hello"}]
        else:
            body["input"] = "hello"
        task = asyncio.create_task(
            client.post(endpoint, headers={"Authorization": "Bearer sk-test"}, json=body)
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            response = await asyncio.wait_for(asyncio.shield(task), 1)
            assert response.status_code == 408, response.text
            assert cancelled.is_set()
            assert not calls
            billing.reserve.assert_not_awaited()
            billing.dispatch.assert_not_awaited()
            billing.accept_selector.assert_not_awaited()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_selector_fallback_authorization_denial_cannot_dispatch_or_bill_hidden_target(
    client, test_app
):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(503, json={"error": {"message": "mock unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        billing = await configure_fallback(test_app, upstream, authorize=False)
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 403, response.text
        assert calls == ["plain"]
        billing.reserve.assert_not_awaited()
