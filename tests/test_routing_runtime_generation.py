from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from src.router.runtime_authorization import CallableTargetGrantSnapshot
from src.router.runtime_generation import pin_routing_runtime_generation


def test_operation_keeps_one_generation_after_store_publication(test_app) -> None:  # noqa: ANN001
    store = test_app.state.routing_runtime_generation_store
    first = store.require_snapshot()
    operation_state = SimpleNamespace()

    pinned = pin_routing_runtime_generation(test_app.state, operation_state)
    replacement = replace(
        first,
        generation_id="replacement-generation",
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
    assert pin_routing_runtime_generation(test_app.state, SimpleNamespace()) is replacement
