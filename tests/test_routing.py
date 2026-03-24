from __future__ import annotations

import pytest

from src.models.errors import ServiceUnavailableError
import src.router.strategies as strategies_module
from src.router import (
    HealthEndpointHandler,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.router.usage import normalize_router_usage


@pytest.mark.asyncio
async def test_deployments_health_endpoint(client):
    response = await client.get("/health/deployments")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"healthy", "degraded"}
    assert payload["total_count"] >= 1
    assert isinstance(payload["deployments"], list)


@pytest.mark.asyncio
async def test_least_busy_strategy_selects_lowest_active_requests():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
                {"deployment_id": "dep-b", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.LEAST_BUSY,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    await state.increment_active("dep-a")
    selected = await router.select_deployment("gpt-4o-mini", {})
    assert selected is not None
    assert selected.deployment_id == "dep-b"


@pytest.mark.asyncio
async def test_request_tags_filter_candidates_before_strategy(monkeypatch):
    monkeypatch.setattr(strategies_module.random, "choice", lambda deployments: deployments[-1])
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-tagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"tags": ["vip"]},
                },
                {
                    "deployment_id": "dep-untagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"tags": []},
                },
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {"metadata": {"tags": ["vip"]}})

    assert selected is not None
    assert selected.deployment_id == "dep-tagged"


@pytest.mark.asyncio
async def test_tag_based_strategy_applies_tag_filtering():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-tagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"tags": ["vip"]},
                },
                {
                    "deployment_id": "dep-untagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                },
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.TAG_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {"metadata": {"tags": ["vip"]}})

    assert selected is not None
    assert selected.deployment_id == "dep-tagged"


def test_tag_based_strategy_is_weighted_selection_on_prefiltered_pool():
    strategy = strategies_module.TagBasedStrategy()
    assert isinstance(strategy.fallback, strategies_module.WeightedStrategy)


@pytest.mark.asyncio
async def test_priority_based_strategy_applies_priority_filtering(monkeypatch):
    monkeypatch.setattr(strategies_module.random, "choice", lambda deployments: deployments[-1])
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-primary",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"priority": 0},
                },
                {
                    "deployment_id": "dep-secondary",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"priority": 10},
                },
            ]
        }
    )
    simple_router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )
    priority_router = Router(
        strategy=RoutingStrategy.PRIORITY_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    simple_selected = await simple_router.select_deployment("gpt-4o-mini", {})
    priority_selected = await priority_router.select_deployment("gpt-4o-mini", {})

    assert simple_selected is not None
    assert simple_selected.deployment_id == "dep-secondary"
    assert priority_selected is not None
    assert priority_selected.deployment_id == "dep-primary"


@pytest.mark.asyncio
async def test_latency_based_strategy_keeps_unsampled_members_eligible(monkeypatch):
    monkeypatch.setattr(strategies_module.random, "uniform", lambda start, end: (start + end) * 0.75)
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-sampled", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
                {"deployment_id": "dep-unsampled", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        }
    )
    await state.record_latency("dep-sampled", 10.0)
    router = Router(
        strategy=RoutingStrategy.LATENCY_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-unsampled"


@pytest.mark.asyncio
async def test_cost_based_strategy_uses_mode_specific_pricing():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "image-group": [
                {
                    "deployment_id": "dep-expensive",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {"mode": "image_generation", "input_cost_per_image": 0.10},
                },
                {
                    "deployment_id": "dep-cheap",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {"mode": "image_generation", "input_cost_per_image": 0.02},
                },
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.COST_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("image-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cheap"


@pytest.mark.asyncio
async def test_usage_based_strategy_uses_router_state_usage():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
            ]
        }
    )
    await state.increment_usage("dep-a", 80)
    await state.increment_usage("dep-a", 0)
    await state.increment_usage("dep-b", 5)
    router = Router(
        strategy=RoutingStrategy.USAGE_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-b"


@pytest.mark.asyncio
async def test_usage_based_strategy_uses_image_limits_when_configured():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "image-group": [
                {
                    "deployment_id": "dep-hot",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {"mode": "image_generation", "rpm_limit": 10, "image_pm_limit": 10},
                },
                {
                    "deployment_id": "dep-cool",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {"mode": "image_generation", "rpm_limit": 10, "image_pm_limit": 10},
                },
            ]
        }
    )
    await state.increment_usage_counters("dep-hot", {"rpm": 1, "image_pm": 9})
    await state.increment_usage_counters("dep-cool", {"rpm": 1, "image_pm": 1})
    router = Router(
        strategy=RoutingStrategy.USAGE_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("image-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cool"


@pytest.mark.asyncio
async def test_rate_limit_aware_strategy_skips_hot_deployments():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-hot",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
                {
                    "deployment_id": "dep-cool",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
            ]
        }
    )
    for _ in range(9):
        await state.increment_usage("dep-hot", 0)
    await state.increment_usage("dep-hot", 95)
    router = Router(
        strategy=RoutingStrategy.RATE_LIMIT_AWARE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cool"


@pytest.mark.asyncio
async def test_rate_limit_aware_strategy_uses_audio_limits_when_configured():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "audio-group": [
                {
                    "deployment_id": "dep-hot",
                    "deltallm_params": {"model": "openai/audio"},
                    "model_info": {"mode": "audio_transcription", "rpm_limit": 10, "audio_seconds_pm_limit": 10},
                },
                {
                    "deployment_id": "dep-cool",
                    "deltallm_params": {"model": "openai/audio"},
                    "model_info": {"mode": "audio_transcription", "rpm_limit": 10, "audio_seconds_pm_limit": 10},
                },
            ]
        }
    )
    await state.increment_usage_counters("dep-hot", {"rpm": 1, "audio_seconds_pm": 9})
    await state.increment_usage_counters("dep-cool", {"rpm": 1, "audio_seconds_pm": 1})
    router = Router(
        strategy=RoutingStrategy.RATE_LIMIT_AWARE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("audio-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cool"


@pytest.mark.asyncio
async def test_rate_limit_aware_strategy_uses_audio_character_limits_when_duration_is_also_present():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "audio-group": [
                {
                    "deployment_id": "dep-hot",
                    "deltallm_params": {"model": "openai/audio"},
                    "model_info": {
                        "mode": "audio_speech",
                        "rpm_limit": 10,
                        "audio_seconds_pm_limit": 10,
                        "char_pm_limit": 20,
                    },
                },
                {
                    "deployment_id": "dep-cool",
                    "deltallm_params": {"model": "openai/audio"},
                    "model_info": {
                        "mode": "audio_speech",
                        "rpm_limit": 10,
                        "audio_seconds_pm_limit": 10,
                        "char_pm_limit": 20,
                    },
                },
            ]
        }
    )
    await state.increment_usage_counters(
        "dep-hot",
        normalize_router_usage(
            mode="audio_speech",
            usage={"duration_seconds": 1.2, "input_characters": 20},
        ),
    )
    await state.increment_usage_counters(
        "dep-cool",
        normalize_router_usage(
            mode="audio_speech",
            usage={"duration_seconds": 1.2, "input_characters": 5},
        ),
    )
    router = Router(
        strategy=RoutingStrategy.RATE_LIMIT_AWARE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("audio-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cool"


@pytest.mark.asyncio
async def test_pre_call_checks_use_router_state_usage():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-over",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 1, "tpm_limit": 10},
                },
                {
                    "deployment_id": "dep-ok",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
            ]
        }
    )
    await state.increment_usage("dep-over", 20)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(enable_pre_call_checks=True),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-ok"


@pytest.mark.asyncio
async def test_pre_call_checks_use_mode_specific_limits():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "rerank-group": [
                {
                    "deployment_id": "dep-over",
                    "deltallm_params": {"model": "openai/rerank"},
                    "model_info": {"mode": "rerank", "rpm_limit": 10, "rerank_units_pm_limit": 5},
                },
                {
                    "deployment_id": "dep-ok",
                    "deltallm_params": {"model": "openai/rerank"},
                    "model_info": {"mode": "rerank", "rpm_limit": 10, "rerank_units_pm_limit": 5},
                },
            ]
        }
    )
    await state.increment_usage_counters("dep-over", {"rpm": 1, "rerank_units_pm": 5})
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(enable_pre_call_checks=True),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("rerank-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-ok"


def test_normalize_router_usage_keeps_non_token_modes_out_of_tpm():
    image_usage = normalize_router_usage(mode="image_generation", usage={"images": 2})
    assert image_usage == {"rpm": 1, "image_pm": 2}

    audio_usage = normalize_router_usage(
        mode="audio_transcription",
        usage={"duration_seconds": 1.2, "prompt_tokens": 99},
    )
    assert audio_usage == {"rpm": 1, "audio_seconds_pm": 2}

    rerank_usage = normalize_router_usage(mode="rerank", usage={"rerank_units": 4, "prompt_tokens": 120})
    assert rerank_usage == {"rpm": 1, "rerank_units_pm": 4}


def test_normalize_router_usage_records_both_audio_duration_and_character_counters():
    audio_usage = normalize_router_usage(
        mode="audio_speech",
        usage={"duration_seconds": 1.2, "input_characters": 11},
    )

    assert audio_usage == {"rpm": 1, "audio_seconds_pm": 2, "char_pm": 11}


def test_normalize_router_usage_counts_multimodal_chat_tokens_without_total_tokens():
    usage = normalize_router_usage(
        mode="chat",
        usage={
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "input_audio_tokens": 5,
            "output_audio_tokens": 7,
        },
    )
    assert usage == {"rpm": 1, "tpm": 17}

    fallback_usage = normalize_router_usage(
        mode="chat",
        usage={"prompt_tokens": 2, "completion_tokens": 3, "audio_tokens": 11},
    )
    assert fallback_usage == {"rpm": 1, "tpm": 16}


def test_build_deployment_registry_supports_explicit_route_groups():
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"weight": 1},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"weight": 1},
                },
            ]
        },
        route_groups=[
            {
                "key": "support-fast",
                "enabled": True,
                "members": [
                    {"deployment_id": "dep-b", "weight": 8},
                    {"deployment_id": "dep-a"},
                ],
            }
        ],
    )

    assert "gpt-4o-mini" in registry
    assert "support-fast" in registry
    assert [item.deployment_id for item in registry["support-fast"]] == ["dep-b", "dep-a"]
    assert registry["support-fast"][0].weight == 8


@pytest.mark.asyncio
async def test_group_policy_overrides_global_strategy():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "members": [
                {"deployment_id": "dep-a"},
                {"deployment_id": "dep-b"},
            ],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"input_cost_per_token": 0.1},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"input_cost_per_token": 0.9},
                },
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.COST_BASED,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )

    await state.increment_active("dep-a")
    await state.increment_active("dep-a")

    selected_in_group = await router.select_deployment("support-route", {})
    selected_legacy = await router.select_deployment("gpt-4o-mini", {})

    assert selected_in_group is not None
    assert selected_in_group.deployment_id == "dep-b"
    assert selected_legacy is not None
    assert selected_legacy.deployment_id == "dep-a"


@pytest.mark.asyncio
async def test_router_records_route_decision_envelope():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "policy_version": 7,
            "members": [{"deployment_id": "dep-a"}],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )
    request_context: dict[str, object] = {}

    selected = await router.select_deployment("support-route", request_context)

    assert selected is not None
    decision = request_context.get("route_decision")
    assert isinstance(decision, dict)
    assert decision["model_group"] == "support-route"
    assert decision["strategy"] == "least-busy"
    assert decision["policy_version"] == 7
    assert decision["selected_deployment_id"] == "dep-a"


@pytest.mark.asyncio
async def test_router_exposes_failover_overrides_from_policy():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "policy_version": 3,
            "timeouts": {"global_ms": 750},
            "retry": {"max_attempts": 2, "retryable_error_classes": ["timeout", "rate_limit"]},
            "members": [{"deployment_id": "dep-a"}],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )
    request_context: dict[str, object] = {}

    selected = await router.select_deployment("support-route", request_context)

    assert selected is not None
    policy = request_context.get("route_policy")
    assert isinstance(policy, dict)
    assert policy["timeout_seconds"] == 0.75
    assert policy["retry_max_attempts"] == 2
    assert policy["retryable_error_classes"] == ["rate_limit", "timeout"]


@pytest.mark.asyncio
async def test_router_state_fail_open_uses_bounded_local_fallback():
    state = RedisStateBackend(redis=None, degraded_mode="fail_open", max_local_latency_samples=2)

    await state.increment_active("dep-a")
    await state.record_latency("dep-a", 10.0)
    await state.record_latency("dep-a", 20.0)
    await state.record_latency("dep-a", 30.0)

    assert await state.get_active_requests("dep-a") == 1
    latency_window = await state.get_latency_window("dep-a", 300_000)
    assert [lat for _, lat in latency_window] == [20.0, 30.0]
    assert state.get_backend_status()["mode"] == "degraded"


@pytest.mark.asyncio
async def test_router_state_fail_closed_raises_when_backend_unavailable():
    state = RedisStateBackend(redis=None, degraded_mode="fail_closed")

    with pytest.raises(ServiceUnavailableError, match="Router state backend unavailable"):
        await state.get_active_requests("dep-a")

    assert state.get_backend_status()["mode"] == "unavailable"


@pytest.mark.asyncio
async def test_router_state_drops_zero_active_local_entries():
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")

    await state.increment_active("dep-a")
    value = await state.decrement_active("dep-a")

    assert value == 0
    assert await state.get_active_requests("dep-a") == 0
    assert "dep-a" not in state._active
    assert "dep-a" not in state._local_last_seen


@pytest.mark.asyncio
async def test_router_state_prunes_stale_local_entries(monkeypatch: pytest.MonkeyPatch):
    state = RedisStateBackend(redis=None, degraded_mode="fail_open", local_state_ttl_sec=1)
    now = {"value": 1_000.0}

    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    await state.record_failure("dep-a", "boom")

    now["value"] = 1_005.0
    health = await state.get_health("dep-a")

    assert health == {}
    assert "dep-a" not in state._local_last_seen


@pytest.mark.asyncio
async def test_health_handler_surfaces_degraded_router_state():
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        }
    )
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    handler = HealthEndpointHandler(deployment_registry=registry, state_backend=state)

    payload = await handler.get_health_status()

    assert payload["status"] == "degraded"
    assert payload["state_backend"]["mode"] == "degraded"
