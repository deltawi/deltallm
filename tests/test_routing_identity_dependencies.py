from __future__ import annotations

from dataclasses import replace

import pytest

from src.providers.request_defaults import provider_request_defaults
from src.router import Deployment, FallbackConfig, RoutingStrategy
from src.router.fallback_identity import fingerprint_fallback_dependencies
from src.router.routing_identity import build_runtime_routing_fingerprints
from src.routers.utils import apply_default_params


def _deployment(*, params=None, info=None):
    return Deployment(
        deployment_id="stable-deployment",
        model_name="public-group",
        deltallm_params={"model": "openai/model-a", "api_key": "secret-a", **(params or {})},
        model_info=info or {},
    )


def _identity(deployment, *, config=FallbackConfig(), pre_call_checks=False):
    return build_runtime_routing_fingerprints(
        groups=[],
        policies={},
        deployments={"public-group": [deployment]},
        default_strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        failover_config=config,
        enable_pre_call_checks=pre_call_checks,
    )["public-group"]


@pytest.mark.parametrize(
    "params",
    [
        {"model": "openai/model-b"},
        {"provider": "vllm"},
        {"api_base": "https://new-provider.example/v1"},
        {"api_version": "new-version"},
        {"region": "another-region"},
        {"auth_header_name": "X-Provider-Token"},
        {"auth_header_format": "Token {api_key}"},
        {"timeout": 3},
        {"stream_timeout": 2},
        {"max_tokens": 42},
    ],
)
def test_response_affecting_provider_configuration_invalidates(params):
    assert _identity(_deployment(params=params)) != _identity(_deployment())


@pytest.mark.parametrize(
    "info",
    [
        {"mode": "embedding"},
        {"max_tokens": 8192},
        {"max_input_tokens": 4096},
        {"max_output_tokens": 1024},
        {"input_cost_per_token": 0.1},
        {"output_cost_per_token": 0.2},
        {"cost_per_request": 0.3},
        {"default_params": {"seed": 1}},
        {"default_params": {"response_format": {"type": "json_object"}}},
    ],
)
def test_execution_defaults_and_context_metadata_invalidate(info):
    assert _identity(_deployment(info=info)) != _identity(_deployment())


@pytest.mark.parametrize(
    "changes",
    [
        {"tags": ["another-region"]},
        {"rpm_limit": 100},
        {"tpm_limit": 1000},
        {"input_cost_per_token": 0.1},
        {"output_cost_per_token": 0.2},
        {"named_credential_id": "another-provider-account"},
    ],
)
def test_effective_candidate_configuration_invalidates(changes):
    assert _identity(replace(_deployment(), **changes)) != _identity(_deployment())


@pytest.mark.parametrize(
    "config",
    [
        FallbackConfig(timeout=5),
        FallbackConfig(num_retries=1),
        FallbackConfig(retry_after=1),
        FallbackConfig(backoff_multiplier=3),
        FallbackConfig(backoff_max=1),
        FallbackConfig(backoff_jitter=False),
    ],
)
def test_inherited_execution_configuration_invalidates(config):
    assert _identity(_deployment(), config=config) != _identity(_deployment())


def test_pre_call_eligibility_changes_invalidate():
    assert _identity(_deployment(), pre_call_checks=True) != _identity(_deployment())


def test_credentials_reloads_and_opaque_metadata_are_not_response_identity(monkeypatch):
    captured = []
    from src.router import routing_identity

    original = routing_identity._digest

    def digest(value):
        captured.append(value)
        return original(value)

    monkeypatch.setattr(routing_identity, "_digest", digest)
    changed = _deployment(
        params={
            "api_key": "secret-b",
            "aws_secret_access_key": "secret-c",
            "operator_note": "note",
        },
        info={"metadata": {"note": "opaque"}, "default_params": {"available_voices": ["voice"]}},
    )
    changed = replace(changed, health_incarnation="new-health-generation")
    assert _identity(changed) == _identity(_deployment())
    for secret in ("secret-a", "secret-b", "secret-c", "new-health-generation", "opaque"):
        assert secret not in repr(captured)


def test_equivalent_defaults_provider_prefixes_and_tag_sets_reuse_identity():
    original = replace(
        _deployment(info={"default_params": {"seed": 1, "stop": ["a", "b"]}}), tags=["us", "eu"]
    )
    equivalent = replace(
        _deployment(
            params={"provider": "openai", "model": "model-a"},
            info={"mode": "chat", "default_params": {"stop": ["a", "b"], "seed": 1}},
        ),
        tags=["eu", "us", "us"],
    )
    assert _identity(original) == _identity(equivalent)
    reversed_stops = replace(
        equivalent, model_info={"default_params": {"seed": 1, "stop": ["b", "a"]}}
    )
    assert _identity(original) != _identity(reversed_stops)
    assert _identity(_deployment(params={"api_base": "https://provider.example/v1/"})) == _identity(
        _deployment(params={"api_base": "https://provider.example/v1"})
    )


def test_provider_defaults_have_one_projection_and_preserve_caller_values():
    info = {"default_params": {"seed": 1, "nested": {"test": True}, "available_voices": ["voice"]}}
    expected = {"seed": 1, "nested": {"test": True}}
    assert provider_request_defaults(info) == apply_default_params({}, info) == expected
    caller = {"seed": 2}
    assert apply_default_params(caller, info) is caller
    assert caller == {**expected, "seed": 2}


@pytest.mark.parametrize(
    "kind", ["fallbacks", "context_window_fallbacks", "content_policy_fallbacks"]
)
def test_fallback_retarget_order_removal_and_transitive_changes(kind):
    local = {key: key for key in ("a", "b", "c", "d", "unrelated")}
    config = FallbackConfig(**{kind: {"a": ["b"], "b": ["c"]}})
    original = fingerprint_fallback_dependencies(local, config)
    modified = fingerprint_fallback_dependencies({**local, "c": "changed"}, config)
    assert original["a"] != modified["a"] and original["b"] != modified["b"]
    assert original["unrelated"] == modified["unrelated"]
    for targets in ([], ["d"], ["b", "d"], ["d", "b"]):
        changed = fingerprint_fallback_dependencies(
            local, FallbackConfig(**{kind: {"a": targets, "b": ["c"]}})
        )
        assert changed["a"] != original["a"]
    first = fingerprint_fallback_dependencies(local, FallbackConfig(**{kind: {"a": ["b", "d"]}}))
    second = fingerprint_fallback_dependencies(local, FallbackConfig(**{kind: {"a": ["d", "b"]}}))
    assert first["a"] != second["a"]


def test_fallback_edge_kind_and_missing_target_creation_change_identity():
    local = {"a": "a"}
    results = [
        fingerprint_fallback_dependencies(local, FallbackConfig(**{kind: {"a": ["missing"]}}))["a"]
        for kind in ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")
    ]
    assert len(set(results)) == 3
    config = FallbackConfig(fallbacks={"a": ["missing"]})
    assert (
        results[0]
        != fingerprint_fallback_dependencies({**local, "missing": "created"}, config)["a"]
    )


def test_fallback_graph_is_stable_across_snapshot_order_and_cycles():
    local = {key: key for key in ("a", "b", "c", "unrelated")}
    config = FallbackConfig(fallbacks={"a": ["b"], "b": ["a", "c"], "c": ["c"]})
    original = fingerprint_fallback_dependencies(local, config)
    reordered = FallbackConfig(fallbacks={"c": ["c"], "b": ["a", "c"], "a": ["b", "b"]})
    assert original == fingerprint_fallback_dependencies(
        dict(reversed(list(local.items()))), reordered
    )
    changed = fingerprint_fallback_dependencies({**local, "c": "replacement"}, config)
    assert all(original[key] != changed[key] for key in ("a", "b", "c"))
    assert original["unrelated"] == changed["unrelated"]
    assert fingerprint_fallback_dependencies({}, FallbackConfig()) == {}
    with pytest.raises(TypeError):
        original["a"] = "mutated"


def test_configured_fallback_key_spelling_is_not_silently_normalized():
    local = {"a": "a", "b": "b"}
    original = fingerprint_fallback_dependencies(local, FallbackConfig(fallbacks={"a": ["b"]}))
    changed = fingerprint_fallback_dependencies(local, FallbackConfig(fallbacks={"a": [" b "]}))
    assert original["a"] != changed["a"]


@pytest.mark.parametrize("cycle", [False, True])
def test_large_shared_fallback_graph_has_linear_hash_work_without_recursion(monkeypatch, cycle):
    from src.router import fallback_identity

    original = fallback_identity._digest
    calls = 0

    def digest(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(fallback_identity, "_digest", digest)
    local = {str(index): str(index) for index in range(2000)}
    edges = {
        str(index): [str(index + step) for step in (1, 2) if index + step < 2000]
        for index in range(2000)
    }
    if cycle:
        edges["1999"] = ["0"]
    identities = fingerprint_fallback_dependencies(local, FallbackConfig(fallbacks=edges))
    assert len(identities) == 2000
    assert calls == (2001 if cycle else 4000)
