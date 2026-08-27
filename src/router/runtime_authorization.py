from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


ScopeKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CallableTargetGrantSnapshot:
    """Immutable authorization inputs published with one routing generation."""

    enabled_by_scope: Mapping[ScopeKey, frozenset[str]]
    binding_counts_by_scope: Mapping[ScopeKey, int]
    scope_modes_by_scope: Mapping[ScopeKey, str]
    enabled_groups_by_scope: Mapping[ScopeKey, frozenset[str]]
    group_binding_counts_by_scope: Mapping[ScopeKey, int]
    callable_keys_by_group: Mapping[str, frozenset[str]]

    @classmethod
    def create(
        cls,
        *,
        enabled_by_scope: Mapping[ScopeKey, frozenset[str]],
        binding_counts_by_scope: Mapping[ScopeKey, int],
        scope_modes_by_scope: Mapping[ScopeKey, str],
        enabled_groups_by_scope: Mapping[ScopeKey, frozenset[str]],
        group_binding_counts_by_scope: Mapping[ScopeKey, int],
        callable_keys_by_group: Mapping[str, frozenset[str]],
    ) -> CallableTargetGrantSnapshot:
        return cls(
            enabled_by_scope=MappingProxyType(dict(enabled_by_scope)),
            binding_counts_by_scope=MappingProxyType(dict(binding_counts_by_scope)),
            scope_modes_by_scope=MappingProxyType(dict(scope_modes_by_scope)),
            enabled_groups_by_scope=MappingProxyType(dict(enabled_groups_by_scope)),
            group_binding_counts_by_scope=MappingProxyType(dict(group_binding_counts_by_scope)),
            callable_keys_by_group=MappingProxyType(dict(callable_keys_by_group)),
        )

    @classmethod
    def empty(cls) -> CallableTargetGrantSnapshot:
        return cls.create(
            enabled_by_scope={},
            binding_counts_by_scope={},
            scope_modes_by_scope={},
            enabled_groups_by_scope={},
            group_binding_counts_by_scope={},
            callable_keys_by_group={},
        )
