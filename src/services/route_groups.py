from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import logging
import time
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.config import AppConfig
from src.db.route_groups import RouteGroupRepository, RouteGroupRuntimeSnapshot
from src.route_group_config import ModelMode
from src.route_policy_contract import RoutePolicyMember, validate_selector_assignments
from src.router.policy_validation import PolicyMemberInventoryItem
from src.router.redis_keys import RouteGroupRuntimeRedisKeyspace
from src.router.selection.policy import (
    SELECTOR_POLICY_SEMANTICS_VERSION,
    RouteSelectorActivationState,
    RouteSelectorActivationUnsupportedError,
    ensure_selector_activation_supported,
)

logger = logging.getLogger(__name__)
ROUTE_GROUP_RUNTIME_CACHE_SCHEMA_VERSION = 2
ROUTE_GROUP_RUNTIME_CACHE_MAX_BYTES = 4 * 1024 * 1024


class _RouteGroupRuntimeCacheMember(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    deployment_id: str = Field(min_length=1)
    enabled: bool = True
    weight: int | None = None
    priority: int | None = None
    # Lanes are accepted only so the activation gate can reject stale active snapshots.
    lane: str | None = None


class _RouteGroupRuntimeCacheGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1)
    mode: str | None = None
    enabled: bool = True
    strategy: str | None = None
    policy_version: int | None = Field(default=None, ge=1)
    policy_semantics_version: int | None = Field(default=None, ge=1)
    timeouts: dict[str, object] | None = None
    retry: dict[str, object] | None = None
    context: dict[str, object] | None = None
    default_prompt: dict[str, str] | None = None
    access_groups: list[str] | None = None
    members: list[_RouteGroupRuntimeCacheMember]
    # These fields are never emitted by the current runtime projection. Accept them only
    # so an old or injected active-selector snapshot reaches the fail-closed gate.
    selector: dict[str, object] | None = None
    policy_json: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_selector_sentinel_fields(self) -> Self:
        policy = self.policy_json if self.policy_json is not None else {"selector": self.selector}
        active_selector = (
            self.policy_semantics_version or 1
        ) >= SELECTOR_POLICY_SEMANTICS_VERSION and policy.get("selector") is not None
        if (self.selector is not None or self.policy_json is not None) and not active_selector:
            raise ValueError("selector sentinel fields require an active version 3 selector")
        if not active_selector and any(member.lane is not None for member in self.members):
            raise ValueError("cached member lanes require an active version 3 selector")
        return self


class _RouteGroupRuntimeCacheEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[2]
    selector_activation_state: Literal["inactive"]
    revision: int = Field(ge=0)
    groups: list[_RouteGroupRuntimeCacheGroup]
    database_initialized: bool | None


@dataclass
class _RuntimeCacheEntry:
    snapshot: RouteGroupRuntimeSnapshot
    expires_at: float


class StaleRouteGroupSnapshotError(RuntimeError):
    """A load completed after a newer local invalidation was requested."""


class UnvalidatedRouteGroupSnapshotError(RuntimeError):
    """A runtime snapshot reached the cache without selector-gate provenance."""


RouteGroupSnapshotSource = Literal[
    "config_only",
    "config_db_empty",
    "config_db_unavailable",
    "db",
    "l1_cache",
    "l2_cache",
]


@dataclass(frozen=True, slots=True)
class RouteGroupSnapshotLoadResult:
    snapshot: RouteGroupRuntimeSnapshot
    source: RouteGroupSnapshotSource
    database_available: bool
    requires_reconciliation: bool

    @property
    def compatibility_source(self) -> str:
        return "config" if self.source.startswith("config_") else self.source


class RouteGroupRuntimeCache:
    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        l1_ttl_seconds: int = 30,
        l2_ttl_seconds: int = 300,
        keyspace: RouteGroupRuntimeRedisKeyspace | None = None,
    ) -> None:
        self.redis = redis_client
        self.l1_ttl_seconds = max(1, int(l1_ttl_seconds))
        self.l2_ttl_seconds = max(1, int(l2_ttl_seconds))
        self.keyspace = keyspace or RouteGroupRuntimeRedisKeyspace()
        self._l1_entry: _RuntimeCacheEntry | None = None
        self._epoch = 0
        self._required_revision = 0

    async def get_snapshot(
        self, repository: RouteGroupRepository
    ) -> tuple[RouteGroupRuntimeSnapshot, str]:
        load_epoch = self._epoch
        durable_revision = await repository.get_runtime_revision()
        required_revision = max(self._required_revision, durable_revision)
        if load_epoch != self._epoch:
            raise StaleRouteGroupSnapshotError("route-group cache invalidated during revision read")

        l1_snapshot = self._read_l1(required_revision=required_revision)
        if l1_snapshot is not None:
            return l1_snapshot, "l1_cache"

        l2_snapshot = await self._read_l2(required_revision)
        if l2_snapshot is not None:
            if load_epoch != self._epoch:
                raise StaleRouteGroupSnapshotError(
                    "route-group cache invalidated during Redis read"
                )
            self._write_l1(l2_snapshot)
            return l2_snapshot, "l2_cache"

        snapshot = await repository.load_runtime_snapshot()
        _require_runtime_snapshot_selector_activation_validated(snapshot)
        if load_epoch != self._epoch or snapshot.revision < self._required_revision:
            raise StaleRouteGroupSnapshotError("stale route-group database load discarded")
        self._required_revision = max(self._required_revision, snapshot.revision)
        self._write_l1(snapshot)
        await self._write_l2(snapshot)
        if load_epoch != self._epoch or snapshot.revision < self._required_revision:
            raise StaleRouteGroupSnapshotError("route-group cache invalidated during Redis write")
        return self._copy_snapshot(snapshot), "db"

    async def get_groups(
        self, repository: RouteGroupRepository
    ) -> tuple[list[dict[str, Any]], str]:
        snapshot, source = await self.get_snapshot(repository)
        return deepcopy(snapshot.groups), source

    async def invalidate(self, *, required_revision: int | None = None) -> bool:
        self._epoch += 1
        self._l1_entry = None
        if required_revision is not None:
            self._required_revision = max(self._required_revision, int(required_revision))
        return True

    def _read_l1(self, *, required_revision: int) -> RouteGroupRuntimeSnapshot | None:
        entry = self._l1_entry
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._l1_entry = None
            return None
        if entry.snapshot.revision < required_revision:
            return None
        return self._copy_snapshot(entry.snapshot)

    def _write_l1(self, snapshot: RouteGroupRuntimeSnapshot) -> None:
        self._l1_entry = _RuntimeCacheEntry(
            snapshot=self._copy_snapshot(snapshot),
            expires_at=time.monotonic() + self.l1_ttl_seconds,
        )

    async def _read_l2(self, revision: int) -> RouteGroupRuntimeSnapshot | None:
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(self._revision_key(revision))
        except Exception as exc:
            logger.debug(
                "route group runtime cache L2 read unavailable: %s",
                type(exc).__name__,
                extra={"cache_tier": "l2", "cache_miss_reason": "redis_unavailable"},
            )
            return None
        if not raw:
            return None
        try:
            serialized = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            if len(serialized) > ROUTE_GROUP_RUNTIME_CACHE_MAX_BYTES:
                logger.debug(
                    "ignored oversized route group runtime cache entry (%s bytes)",
                    len(serialized),
                    extra={"cache_tier": "l2", "cache_miss_reason": "oversized"},
                )
                return None
            payload = _RouteGroupRuntimeCacheEnvelope.model_validate_json(serialized)
            if payload.revision != revision:
                logger.debug(
                    "ignored route group runtime cache revision mismatch",
                    extra={"cache_tier": "l2", "cache_miss_reason": "revision_mismatch"},
                )
                return None
            snapshot = RouteGroupRuntimeSnapshot(
                revision=revision,
                groups=[
                    group.model_dump(mode="python", exclude_unset=True) for group in payload.groups
                ],
                database_initialized=payload.database_initialized,
                selector_activation_state=RouteSelectorActivationState.INACTIVE,
            )
            _ensure_runtime_snapshot_selector_activation_supported(snapshot)
        except RouteSelectorActivationUnsupportedError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            logger.debug(
                "ignored invalid route group runtime cache envelope: %s",
                type(exc).__name__,
                extra={"cache_tier": "l2", "cache_miss_reason": "invalid_payload"},
            )
            return None
        return snapshot

    async def _write_l2(self, snapshot: RouteGroupRuntimeSnapshot) -> bool:
        if self.redis is None:
            return True
        _require_runtime_snapshot_selector_activation_validated(snapshot)
        try:
            serialized = _RouteGroupRuntimeCacheEnvelope(
                schema_version=ROUTE_GROUP_RUNTIME_CACHE_SCHEMA_VERSION,
                selector_activation_state=RouteSelectorActivationState.INACTIVE,
                revision=snapshot.revision,
                groups=snapshot.groups,
                database_initialized=snapshot.database_initialized,
            ).model_dump_json(exclude_unset=True)
            serialized_size = len(serialized.encode("utf-8"))
            if serialized_size > ROUTE_GROUP_RUNTIME_CACHE_MAX_BYTES:
                logger.debug(
                    "skipped oversized route group runtime cache write (%s bytes)",
                    serialized_size,
                )
                return False
            await self.redis.setex(
                self._revision_key(snapshot.revision),
                self.l2_ttl_seconds,
                serialized,
            )
        except Exception as exc:
            logger.debug("failed to write route group runtime cache into redis: %s", exc)
            return False
        return True

    def _revision_key(self, revision: int) -> str:
        return self.keyspace.snapshot(revision)

    @staticmethod
    def _copy_snapshot(snapshot: RouteGroupRuntimeSnapshot) -> RouteGroupRuntimeSnapshot:
        return RouteGroupRuntimeSnapshot(
            revision=snapshot.revision,
            groups=deepcopy(snapshot.groups),
            database_initialized=snapshot.database_initialized,
            selector_activation_state=snapshot.selector_activation_state,
        )


def route_groups_from_config(
    cfg: AppConfig,
    *,
    deployment_modes: Mapping[str, ModelMode] | None = None,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for item in cfg.router_settings.route_groups:
        policy = item.model_dump(mode="python")
        if item.selector is not None:
            if deployment_modes is None:
                raise ValueError(
                    f"selector route group '{item.key}' requires loaded deployment inventory"
                )
            inventory = {
                member.deployment_id: PolicyMemberInventoryItem(
                    deployment_id=member.deployment_id,
                    enabled=member.enabled,
                    workload_mode=deployment_modes[member.deployment_id],
                )
                for member in item.members
                if member.deployment_id in deployment_modes
            }
            validate_selector_assignments(
                item.selector,
                [RoutePolicyMember.model_validate(member.model_dump()) for member in item.members],
                group_mode=item.mode,
                available_members=inventory,
            )
        ensure_selector_activation_supported(
            policy,
            semantics_version=SELECTOR_POLICY_SEMANTICS_VERSION,
        )
        if policy.get("context") is None:
            policy.pop("context", None)
        policy.pop("selector", None)
        for member in policy["members"]:
            member.pop("lane", None)
        groups.append(policy)
    return groups


def _ensure_runtime_snapshot_selector_activation_supported(
    snapshot: RouteGroupRuntimeSnapshot,
) -> None:
    for group in snapshot.groups:
        semantics_version = int(group.get("policy_semantics_version") or 1)
        policy_json = group.get("policy_json")
        policy = policy_json if isinstance(policy_json, dict) else group
        ensure_selector_activation_supported(
            policy,
            semantics_version=semantics_version,
        )


def _require_runtime_snapshot_selector_activation_validated(
    snapshot: RouteGroupRuntimeSnapshot,
) -> None:
    if snapshot.selector_activation_state != RouteSelectorActivationState.INACTIVE:
        raise UnvalidatedRouteGroupSnapshotError(
            "route-group runtime snapshot has not passed the selector activation gate"
        )


async def load_route_group_snapshot(
    repository: RouteGroupRepository | None,
    cfg: AppConfig,
    route_group_cache: RouteGroupRuntimeCache | None = None,
    *,
    allow_config_fallback: bool = True,
    deployment_modes: Mapping[str, ModelMode] | None = None,
) -> tuple[RouteGroupRuntimeSnapshot, str]:
    result = await load_route_group_snapshot_result(
        repository,
        cfg,
        route_group_cache,
        allow_config_fallback=allow_config_fallback,
        deployment_modes=deployment_modes,
    )
    return result.snapshot, result.compatibility_source


async def load_route_group_snapshot_result(
    repository: RouteGroupRepository | None,
    cfg: AppConfig,
    route_group_cache: RouteGroupRuntimeCache | None = None,
    *,
    allow_config_fallback: bool = True,
    deployment_modes: Mapping[str, ModelMode] | None = None,
) -> RouteGroupSnapshotLoadResult:
    if repository is None:
        return RouteGroupSnapshotLoadResult(
            snapshot=RouteGroupRuntimeSnapshot(
                revision=0,
                groups=route_groups_from_config(cfg, deployment_modes=deployment_modes),
                selector_activation_state=RouteSelectorActivationState.INACTIVE,
            ),
            source="config_only",
            database_available=True,
            requires_reconciliation=False,
        )

    try:
        if route_group_cache is None:
            snapshot = await repository.load_runtime_snapshot()
            source = "db"
        else:
            snapshot, source = await route_group_cache.get_snapshot(repository)
        _require_runtime_snapshot_selector_activation_validated(snapshot)
    except (RouteSelectorActivationUnsupportedError, UnvalidatedRouteGroupSnapshotError):
        raise
    except Exception as exc:
        if not allow_config_fallback:
            raise
        logger.warning("failed to load route groups from db, falling back to config: %s", exc)
        return RouteGroupSnapshotLoadResult(
            snapshot=RouteGroupRuntimeSnapshot(
                revision=0,
                groups=route_groups_from_config(cfg, deployment_modes=deployment_modes),
                selector_activation_state=RouteSelectorActivationState.INACTIVE,
            ),
            source="config_db_unavailable",
            database_available=False,
            requires_reconciliation=True,
        )

    if snapshot.database_initialized or not allow_config_fallback:
        return RouteGroupSnapshotLoadResult(
            snapshot=snapshot,
            source=source,
            database_available=True,
            requires_reconciliation=False,
        )
    return RouteGroupSnapshotLoadResult(
        snapshot=RouteGroupRuntimeSnapshot(
            revision=snapshot.revision,
            groups=route_groups_from_config(cfg, deployment_modes=deployment_modes),
            selector_activation_state=RouteSelectorActivationState.INACTIVE,
        ),
        source="config_db_empty",
        database_available=True,
        requires_reconciliation=False,
    )


async def load_route_groups(
    repository: RouteGroupRepository | None,
    cfg: AppConfig,
    route_group_cache: RouteGroupRuntimeCache | None = None,
    *,
    deployment_modes: Mapping[str, ModelMode] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    snapshot, source = await load_route_group_snapshot(
        repository,
        cfg,
        route_group_cache,
        deployment_modes=deployment_modes,
    )
    return snapshot.groups, source
