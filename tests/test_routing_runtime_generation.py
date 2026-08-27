from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from src.router.failover import FailoverManager
from src.router.runtime_authorization import CallableTargetGrantSnapshot
from src.router.runtime_generation import pin_routing_runtime_generation


def test_operation_keeps_one_generation_after_store_publication(test_app) -> None:  # noqa: ANN001
    store = test_app.state.routing_runtime_generation_store
    first = store.require_snapshot()
    operation_state = SimpleNamespace()

    pinned = pin_routing_runtime_generation(test_app.state, operation_state)
    replacement_failover_config = replace(
        first.failover_config,
        fallbacks={"primary": ["general"]},
        context_window_fallbacks={"primary": ["context"]},
        content_policy_fallbacks={"primary": ["content"]},
    )
    replacement_failover_manager = FailoverManager(
        config=replacement_failover_config,
        candidate_planner=first.router,
        state_backend=first.failover_manager.state,
        cooldown_manager=first.cooldown_manager,
        event_journal=first.failover_manager.event_journal,
    )
    replacement = replace(
        first,
        generation_id="replacement-generation",
        failover_config=replacement_failover_config,
        failover_manager=replacement_failover_manager,
        authorization_snapshot=CallableTargetGrantSnapshot.create(
            enabled_by_scope={("organization", "org-default"): frozenset({"replacement-model"})},
            binding_counts_by_scope={("organization", "org-default"): 1},
            scope_modes_by_scope={},
            enabled_groups_by_scope={},
            group_binding_counts_by_scope={},
            callable_keys_by_group={},
        ),
    )
    store.replace(replacement)

    assert pinned is first
    assert pin_routing_runtime_generation(test_app.state, operation_state) is first
    latest = pin_routing_runtime_generation(test_app.state, SimpleNamespace())
    assert latest is replacement
    assert pinned.failover_config.fallbacks == {}
    assert pinned.failover_config.context_window_fallbacks == {}
    assert pinned.failover_config.content_policy_fallbacks == {}
    assert latest.failover_config.fallbacks == {"primary": ("general",)}
    assert latest.failover_config.context_window_fallbacks == {"primary": ("context",)}
    assert latest.failover_config.content_policy_fallbacks == {"primary": ("content",)}
    assert latest.failover_manager.config is latest.failover_config
