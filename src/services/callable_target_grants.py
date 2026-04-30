from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping

from src.models.responses import UserAPIKeyAuth
from src.governance.access_groups import build_callable_keys_by_access_group
from src.services.runtime_scopes import resolve_runtime_scope_context

if TYPE_CHECKING:
    from src.db.callable_target_access_groups import CallableTargetAccessGroupBindingRepository
    from src.db.callable_targets import CallableTargetBindingRepository
    from src.db.callable_target_policies import CallableTargetScopePolicyRepository


@dataclass(frozen=True, slots=True)
class CallableTargetGrantSnapshot:
    enabled_by_scope: dict[tuple[str, str], frozenset[str]]
    binding_counts_by_scope: dict[tuple[str, str], int]
    scope_modes_by_scope: dict[tuple[str, str], str]
    enabled_groups_by_scope: dict[tuple[str, str], frozenset[str]]
    group_binding_counts_by_scope: dict[tuple[str, str], int]
    callable_keys_by_group: dict[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class CallableTargetPolicyResolution:
    allowlist: frozenset[str] | None
    authoritative: bool
    fallback_reason: str | None = None


class CallableTargetGrantService:
    def __init__(
        self,
        repository: CallableTargetBindingRepository | None,
        *,
        policy_repository: CallableTargetScopePolicyRepository | None = None,
        access_group_repository: CallableTargetAccessGroupBindingRepository | None = None,
        callable_target_catalog_getter: Callable[[], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.repository = repository
        self.policy_repository = policy_repository
        self.access_group_repository = access_group_repository
        self.callable_target_catalog_getter = callable_target_catalog_getter
        self._reload_lock = asyncio.Lock()
        self._snapshot = self._empty_snapshot()

    async def reload(self) -> None:
        if (
            self.repository is None
            and self.policy_repository is None
            and self.access_group_repository is None
        ):
            self._snapshot = self._empty_snapshot()
            return

        async with self._reload_lock:
            enabled_by_scope: dict[tuple[str, str], set[str]] = defaultdict(set)
            binding_counts_by_scope: dict[tuple[str, str], int] = defaultdict(int)
            scope_modes_by_scope: dict[tuple[str, str], str] = {}
            enabled_groups_by_scope: dict[tuple[str, str], set[str]] = defaultdict(set)
            group_binding_counts_by_scope: dict[tuple[str, str], int] = defaultdict(int)
            if self.repository is not None:
                offset = 0
                limit = 1000

                while True:
                    bindings, total = await self.repository.list_bindings(limit=limit, offset=offset)
                    for binding in bindings:
                        scope = (binding.scope_type, binding.scope_id)
                        binding_counts_by_scope[scope] += 1
                        if binding.enabled:
                            enabled_by_scope[scope].add(binding.callable_key)
                    offset += len(bindings)
                    if not bindings or offset >= total:
                        break

            if self.access_group_repository is not None:
                offset = 0
                limit = 1000
                while True:
                    group_bindings, total = await self.access_group_repository.list_bindings(
                        limit=limit,
                        offset=offset,
                    )
                    for binding in group_bindings:
                        scope = (binding.scope_type, binding.scope_id)
                        group_binding_counts_by_scope[scope] += 1
                        if binding.enabled:
                            enabled_groups_by_scope[scope].add(binding.group_key)
                    offset += len(group_bindings)
                    if not group_bindings or offset >= total:
                        break

            if self.policy_repository is not None:
                offset = 0
                limit = 1000
                while True:
                    policies, total = await self.policy_repository.list_policies(limit=limit, offset=offset)
                    for policy in policies:
                        scope_modes_by_scope[(policy.scope_type, policy.scope_id)] = policy.mode
                    offset += len(policies)
                    if not policies or offset >= total:
                        break

            callable_keys_by_group = build_callable_keys_by_access_group(
                self._callable_target_catalog()
            )
            for scope, group_keys in enabled_groups_by_scope.items():
                for group_key in group_keys:
                    enabled_by_scope[scope].update(callable_keys_by_group.get(group_key, ()))

            self._snapshot = CallableTargetGrantSnapshot(
                enabled_by_scope={scope: frozenset(values) for scope, values in enabled_by_scope.items()},
                binding_counts_by_scope=dict(binding_counts_by_scope),
                scope_modes_by_scope=scope_modes_by_scope,
                enabled_groups_by_scope={
                    scope: frozenset(values) for scope, values in enabled_groups_by_scope.items()
                },
                group_binding_counts_by_scope=dict(group_binding_counts_by_scope),
                callable_keys_by_group=callable_keys_by_group,
            )

    async def invalidate_all(self) -> None:
        await self.reload()

    def resolve_explicit_allowlist(self, auth: UserAPIKeyAuth) -> set[str] | None:
        if resolve_runtime_scope_context(auth).is_master_key:
            return None

        snapshot = self._snapshot
        scopes = self._applicable_scopes(auth)
        if not scopes:
            return None

        any_matching_bindings = any(self._has_scope_access_configuration(snapshot, scope) for scope in scopes)
        if not any_matching_bindings:
            return None

        allowed: set[str] = set()
        for scope in scopes:
            allowed.update(snapshot.enabled_by_scope.get(scope, ()))
        return allowed

    def get_scope_mode(self, scope_type: str, scope_id: str) -> str | None:
        normalized_scope_type = str(scope_type or "").strip()
        normalized_scope_id = str(scope_id or "").strip()
        if not normalized_scope_type or not normalized_scope_id:
            return None
        return self._snapshot.scope_modes_by_scope.get((normalized_scope_type, normalized_scope_id))

    def has_scope_bindings(self, scope_type: str, scope_id: str | None) -> bool:
        normalized_scope_type = str(scope_type or "").strip()
        normalized_scope_id = str(scope_id or "").strip() if scope_id is not None else ""
        if not normalized_scope_type or not normalized_scope_id:
            return False
        return self._has_scope_access_configuration(
            self._snapshot,
            (normalized_scope_type, normalized_scope_id),
        )

    def resolve_policy_allowlist(self, auth: UserAPIKeyAuth) -> CallableTargetPolicyResolution:
        scope_context = resolve_runtime_scope_context(auth)
        if scope_context.is_master_key:
            return CallableTargetPolicyResolution(allowlist=None, authoritative=True)

        snapshot = self._snapshot
        effective = self._resolve_base_allowlist(auth, snapshot)
        if effective is None:
            return CallableTargetPolicyResolution(
                allowlist=frozenset(),
                authoritative=True,
            )

        if scope_context.team_id is not None and self._get_scope_mode(snapshot, "team", scope_context.team_id) == "restrict":
            effective.intersection_update(snapshot.enabled_by_scope.get(("team", scope_context.team_id), ()))

        if scope_context.api_key_scope_id is not None and self._get_scope_mode(snapshot, "api_key", scope_context.api_key_scope_id) == "restrict":
            effective.intersection_update(snapshot.enabled_by_scope.get(("api_key", scope_context.api_key_scope_id), ()))

        user_scope = ("user", scope_context.user_id) if scope_context.user_id is not None else None
        if user_scope is not None and self._should_restrict_user_scope(snapshot, user_scope):
            effective.intersection_update(snapshot.enabled_by_scope.get(user_scope, ()))

        return CallableTargetPolicyResolution(
            allowlist=frozenset(effective),
            authoritative=True,
        )

    def _resolve_base_allowlist(
        self,
        auth: UserAPIKeyAuth,
        snapshot: CallableTargetGrantSnapshot,
    ) -> set[str] | None:
        scope_context = resolve_runtime_scope_context(auth)
        if scope_context.organization_id is not None:
            organization_scope = ("organization", scope_context.organization_id)
            if not self._has_scope_access_configuration(snapshot, organization_scope):
                return set()
            return set(snapshot.enabled_by_scope.get(organization_scope, ()))

        direct_scopes = self._applicable_scopes(auth)
        direct_allowlists = [
            set(snapshot.enabled_by_scope.get(scope, ()))
            for scope in direct_scopes
            if self._has_scope_access_configuration(snapshot, scope)
        ]
        if not direct_allowlists:
            return None

        effective = set(direct_allowlists[0])
        for allowlist in direct_allowlists[1:]:
            effective.intersection_update(allowlist)
        return effective

    @staticmethod
    def _applicable_scopes(auth: UserAPIKeyAuth) -> tuple[tuple[str, str], ...]:
        scope_context = resolve_runtime_scope_context(auth)
        return scope_context.scope_chain

    @staticmethod
    def _get_scope_mode(
        snapshot: CallableTargetGrantSnapshot,
        scope_type: str,
        scope_id: str,
    ) -> str | None:
        normalized_scope_type = str(scope_type or "").strip()
        normalized_scope_id = str(scope_id or "").strip()
        if not normalized_scope_type or not normalized_scope_id:
            return None
        return snapshot.scope_modes_by_scope.get((normalized_scope_type, normalized_scope_id))

    @staticmethod
    def _should_restrict_user_scope(
        snapshot: CallableTargetGrantSnapshot,
        user_scope: tuple[str, str],
    ) -> bool:
        mode = snapshot.scope_modes_by_scope.get(user_scope)
        if mode == "restrict":
            return True
        return mode is None and CallableTargetGrantService._has_scope_access_configuration(
            snapshot,
            user_scope,
        )

    @staticmethod
    def _empty_snapshot() -> CallableTargetGrantSnapshot:
        return CallableTargetGrantSnapshot(
            enabled_by_scope={},
            binding_counts_by_scope={},
            scope_modes_by_scope={},
            enabled_groups_by_scope={},
            group_binding_counts_by_scope={},
            callable_keys_by_group={},
        )

    def _callable_target_catalog(self) -> Mapping[str, Any] | None:
        if self.callable_target_catalog_getter is None:
            return None
        return self.callable_target_catalog_getter()

    @staticmethod
    def _has_scope_access_configuration(
        snapshot: CallableTargetGrantSnapshot,
        scope: tuple[str, str],
    ) -> bool:
        return (
            snapshot.binding_counts_by_scope.get(scope, 0)
            + snapshot.group_binding_counts_by_scope.get(scope, 0)
        ) > 0
