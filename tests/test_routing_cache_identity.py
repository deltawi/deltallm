from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.cache import CacheKeyBuilder, InMemoryBackend, NoopCacheMetrics, StreamingCacheHandler
from src.callbacks import CallbackManager, CustomLogger
from src.router import (
    FallbackConfig,
    FailoverManager,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.router.registry import DeploymentRegistryStore
from src.router.routing_identity import build_runtime_routing_fingerprints
from src.router.runtime_generation import RoutingRuntimeGeneration
from src.router.selection.policy import build_routing_fingerprint


def _group():
    return {
        "key": "gpt-4o-mini",
        "mode": "chat",
        "policy_semantics_version": 3,
        "policy_version": 1,
        "members": [{"deployment_id": "gpt-4o-mini-0"}],
    }


def _fingerprints(groups, *, default_strategy=RoutingStrategy.SIMPLE_SHUFFLE):
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deltallm_params": {"model": "openai/small"},
                    "model_info": {"mode": "chat", "weight": 3},
                }
            ]
        },
        route_groups=groups,
    )
    return build_runtime_routing_fingerprints(
        groups=groups,
        policies=build_route_group_policies(groups),
        deployments=registry,
        default_strategy=default_strategy,
        failover_config=FallbackConfig(),
    )


def test_runtime_identity_reuses_canonical_fingerprint_and_effective_member_weights(monkeypatch):
    group = _group()
    expected = build_routing_fingerprint(
        workload_mode="chat",
        strategy="simple-shuffle",
        semantics_version=3,
        effective_members=[
            {"deployment_id": "gpt-4o-mini-0", "enabled": True, "weight": 3, "priority": 0}
        ],
    )
    observed = []

    def fingerprint(**kwargs):
        result = build_routing_fingerprint(**kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr("src.router.routing_identity.build_routing_fingerprint", fingerprint)
    identities = _fingerprints([group])
    assert observed == [expected]
    assert identities["gpt-4o-mini"].startswith("route-response-v1:")
    with pytest.raises(TypeError):
        identities["gpt-4o-mini"] = "mutated"


@pytest.mark.parametrize(
    "update",
    [
        {"policy_version": 9},
        {"revision": 99, "generation_id": "new", "source": "redis"},
        {"metadata": {"operator_note": "opaque"}},
        {"strategy": "simple-shuffle"},
        {"members": [{"deployment_id": "gpt-4o-mini-0", "weight": 3, "priority": 0}]},
    ],
)
def test_equivalent_effective_policy_reuses_identity(update):
    assert _fingerprints([_group()]) == _fingerprints([{**_group(), **update}])


@pytest.mark.parametrize(
    "update",
    [
        {"strategy": "least-busy"},
        {"timeouts": {"global_ms": 4000}},
        {"retry": {"max_attempts": 3}},
        {"retry": {"retryable_error_classes": ["timeout"]}},
        {"members": [{"deployment_id": "gpt-4o-mini-0", "weight": 5}]},
        {"members": [{"deployment_id": "gpt-4o-mini-0", "priority": 2}]},
        {"members": [{"deployment_id": "gpt-4o-mini-0", "enabled": False}]},
        {"context": {"mode": "smallest-sufficient", "unknown_capacity": "exclude"}},
    ],
)
def test_effective_policy_change_invalidates_identity(update):
    assert _fingerprints([_group()]) != _fingerprints([{**_group(), **update}])


def test_inherited_strategy_and_disabled_group_ownership_affect_identity():
    group = _group()
    assert _fingerprints([group]) != _fingerprints(
        [group], default_strategy=RoutingStrategy.LEAST_BUSY
    )
    assert _fingerprints([{**group, "enabled": False}]) == _fingerprints([])


def test_version_aware_selector_and_lane_fingerprint_without_activation():
    group = _group()
    group["selector"] = {
        "kind": "llm-tier",
        "classifier_deployment_id": "gpt-4o-mini-0",
        "lanes": [
            {"id": "economy", "rank": 0, "description": "Routine"},
            {"id": "quality", "rank": 1, "description": "Complex"},
        ],
    }
    group["members"][0]["lane"] = "economy"
    changed = deepcopy(group)
    changed["members"][0]["lane"] = "quality"
    assert _fingerprints([group]) != _fingerprints([changed])
    changed = deepcopy(group)
    changed["selector"]["timeout_ms"] = 1000
    assert _fingerprints([group]) != _fingerprints([changed])
    historical = {**group, "policy_semantics_version": 1}
    assert _fingerprints([historical]) == _fingerprints(
        [{**historical, "selector": {"opaque": True}, "members": _group()["members"]}]
    )
    # Current file configuration has no stored database semantics version.
    file_group = {key: value for key, value in group.items() if key != "policy_semantics_version"}
    assert _fingerprints([file_group]) == _fingerprints([group])


@pytest.mark.parametrize("custom_key", [None, "private custom key"])
def test_routing_dimension_cannot_be_removed_by_custom_key_or_field_configuration(custom_key):
    builder = CacheKeyBuilder(fields={"messages"}, custom_salt="salt")
    payload = {"messages": [], "routing_fingerprint": "caller-controlled"}
    first = builder.build_key_from_payload(payload, custom_key, routing_fingerprint="policy-a")
    second = builder.build_key_from_payload(payload, custom_key, routing_fingerprint="policy-b")
    assert first != second
    assert len(first) == 64 and "private" not in first and "policy-a" not in first
    assert first == builder.build_key_from_payload(
        {**payload, "routing_fingerprint": "changed"}, custom_key, routing_fingerprint="policy-a"
    )
    assert first != CacheKeyBuilder(
        fields={"messages"}, custom_salt="other"
    ).build_key_from_payload(payload, custom_key, routing_fingerprint="policy-a")


def _publish(test_app, groups, *, aliases=None, failover_config=None):
    old = test_app.state.routing_runtime_generation_store.require_snapshot()
    failover_config = failover_config or old.failover_config
    registry = DeploymentRegistryStore(
        build_deployment_registry(test_app.state.model_registry, route_groups=groups)
    )
    config = RouterConfig(
        route_group_policies=build_route_group_policies(groups), model_group_alias=aliases or {}
    )
    router = Router(old.strategy, old.router.state, config, registry)
    manager = FailoverManager(
        config=failover_config,
        candidate_planner=router,
        state_backend=old.router.state,
        cooldown_manager=old.cooldown_manager,
    )
    new = RoutingRuntimeGeneration.create(
        revision=old.revision + 1,
        app_config=old.app_config,
        model_registry=test_app.state.model_registry,
        route_groups=groups,
        callable_target_catalog=dict(old.callable_target_catalog),
        authorization_snapshot=old.authorization_snapshot,
        deployment_registry=registry,
        strategy=old.strategy,
        router_config=config,
        failover_config=failover_config,
        salt_key=old.salt_key,
        router=router,
        failover_manager=manager,
        cooldown_manager=old.cooldown_manager,
    )
    test_app.state.routing_runtime_generation_store.replace(new)
    return new


def _enable_cache(test_app):
    # These scenarios exercise up to four independently admitted outer requests.
    # Keep rate admission enabled; give this test key enough capacity for the scenario.
    next(iter(test_app.state._test_repo.records.values())).rpm_limit = 10
    backend = InMemoryBackend(max_size=100)
    test_app.state.cache_backend = backend
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="routing-cache-test")
    test_app.state.cache_metrics = NoopCacheMetrics()
    test_app.state.streaming_cache_handler = StreamingCacheHandler(backend)
    return backend


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/responses", "/v1/completions"])
@pytest.mark.parametrize("custom", [False, True])
async def test_http_cache_policy_publish_equivalent_reload_and_rollback(
    client, test_app, endpoint, custom
):
    backend = _enable_cache(test_app)
    _publish(test_app, [_group()])
    body = {"model": "gpt-4o-mini"}
    if endpoint == "/v1/responses":
        body["input"] = "cached request"
    elif endpoint == "/v1/completions":
        body["prompt"] = "cached request"
    else:
        body["messages"] = [{"role": "user", "content": "cached request"}]
    if custom:
        body["metadata"] = {"cache_key": "same private key"}
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}

    async def send(expected):
        response = await client.post(endpoint, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["x-deltallm-cache-hit"] == expected
        return response

    await send("false")
    _publish(test_app, [{**_group(), "policy_version": 2}])
    await send("true")
    _publish(test_app, [{**_group(), "strategy": "least-busy"}])
    await send("false")
    _publish(test_app, [_group()])
    await send("true")
    assert test_app.state.http_client.post_calls == 2
    assert len(backend._cache) == 2


async def test_streaming_cache_respects_policy_identity_and_reuses_equivalent_generations(
    client, test_app
):
    _enable_cache(test_app)
    _publish(test_app, [_group()])
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "stream": True,
        "messages": [{"role": "user", "content": "stream identity"}],
        "metadata": {"cache_key": "same"},
    }
    for group, expected in [
        (_group(), "false"),
        (_group(), "true"),
        ({**_group(), "strategy": "least-busy"}, "false"),
    ]:
        _publish(test_app, [group])
        response = await client.post("/v1/chat/completions", json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["x-deltallm-cache-hit"] == expected
        assert "[DONE]" in response.text
    assert test_app.state.http_client.stream_calls == 2


@pytest.mark.parametrize("version", ["v2", "v3"])
async def test_prior_namespace_is_not_read(client, test_app, version):
    backend = _enable_cache(test_app)
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "version"}]}
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    await client.post("/v1/chat/completions", json=body, headers=headers)
    key, entry = next(iter(backend._cache.items()))
    assert "schema:v4:" in key
    backend._cache[key.replace("schema:v4:", f"schema:{version}:")] = entry
    del backend._cache[key]
    response = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers["x-deltallm-cache-hit"] == "false"
    assert test_app.state.http_client.post_calls == 2


class _PublishDuringPreflight(CustomLogger):
    def __init__(self, test_app):
        self.test_app = test_app

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        _publish(self.test_app, [{**_group(), "strategy": "least-busy"}])
        return data


async def test_cache_identity_remains_pinned_across_preflight_reload(client, test_app):
    _enable_cache(test_app)
    initial = _publish(test_app, [_group()])
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "pinned"}]}
    await client.post("/v1/chat/completions", json=body, headers=headers)
    callbacks = CallbackManager()
    callbacks.register_callback(_PublishDuringPreflight(test_app), callback_type="success")
    test_app.state.callback_manager = callbacks
    response = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers["x-deltallm-cache-hit"] == "true"
    assert test_app.state.routing_runtime_generation_store.require_snapshot() is not initial
    assert test_app.state.http_client.post_calls == 1


async def test_cache_hit_never_constructs_or_invokes_selector(client, test_app, monkeypatch):
    _enable_cache(test_app)
    selector = AsyncMock(side_effect=AssertionError("selector must remain unreachable"))
    monkeypatch.setattr("src.router.selection.service.SelectorService.select_once", selector)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "no selector"}]}
    for expected in ("false", "true"):
        response = await client.post("/v1/chat/completions", json=body, headers=headers)
        assert response.status_code == 200
        assert response.headers["x-deltallm-cache-hit"] == expected
    selector.assert_not_called()


async def test_alias_retargeting_uses_the_new_effective_routing_identity(client, test_app):
    _enable_cache(test_app)
    groups = [
        {**_group(), "key": "route-a"},
        {**_group(), "key": "route-b", "strategy": "least-busy"},
    ]
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "alias"}],
        "metadata": {"cache_key": "same"},
    }
    for target, expected in [("route-a", "false"), ("route-b", "false"), ("route-a", "true")]:
        _publish(test_app, groups, aliases={"gpt-4o-mini": target})
        response = await client.post("/v1/chat/completions", json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["x-deltallm-cache-hit"] == expected
    assert test_app.state.http_client.post_calls == 2


def test_unrelated_group_changes_leave_primary_identity_unchanged():
    other = {**_group(), "key": "unrelated"}
    original = _fingerprints([_group(), other])
    changed = _fingerprints([_group(), {**other, "strategy": "least-busy"}])
    assert original["gpt-4o-mini"] == changed["gpt-4o-mini"]
    assert original["unrelated"] != changed["unrelated"]


@pytest.mark.parametrize(
    "module",
    ["router/routing_identity.py", "router/fallback_identity.py", "providers/request_defaults.py"],
)
def test_fingerprint_projection_is_small_request_free_and_dependency_free(module):
    path = Path(__file__).parents[1] / "src" / module
    source = path.read_text()
    assert len(source.splitlines()) < 500
    for node in ast.walk(ast.parse(source)):
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await))
        if isinstance(node, ast.FunctionDef):
            assert node.end_lineno - node.lineno < 80
        if isinstance(node, ast.Name):
            assert node.id not in {"Any", "Request", "getattr", "hasattr", "setattr"}
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                ("fastapi", "src.db", "redis", "prisma", "httpx", "src.bootstrap")
            )
