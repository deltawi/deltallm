from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Literal

from src.config import AppConfig
from src.db.route_groups import RouteGroupRepository, RouteGroupRuntimeSnapshot

logger = logging.getLogger(__name__)
ROUTE_GROUP_RUNTIME_CACHE_KEY = "deltallm:routegroup:v1:runtime"


@dataclass
class _RuntimeCacheEntry:
    snapshot: RouteGroupRuntimeSnapshot
    expires_at: float


class StaleRouteGroupSnapshotError(RuntimeError):
    """A load completed after a newer local invalidation was requested."""


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
        cache_key: str = ROUTE_GROUP_RUNTIME_CACHE_KEY,
    ) -> None:
        self.redis = redis_client
        self.l1_ttl_seconds = max(1, int(l1_ttl_seconds))
        self.l2_ttl_seconds = max(1, int(l2_ttl_seconds))
        self.cache_key = cache_key
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
            if not raw:
                return None
            payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception as exc:
            logger.debug("failed to read route group runtime cache from redis: %s", exc)
            return None
        if not isinstance(payload, dict) or int(payload.get("revision", -1)) != revision:
            return None
        groups = payload.get("groups")
        if not isinstance(groups, list):
            return None
        return RouteGroupRuntimeSnapshot(
            revision=revision,
            groups=[item for item in groups if isinstance(item, dict)],
            database_initialized=(
                bool(payload["database_initialized"]) if "database_initialized" in payload else None
            ),
        )

    async def _write_l2(self, snapshot: RouteGroupRuntimeSnapshot) -> bool:
        if self.redis is None:
            return True
        try:
            await self.redis.setex(
                self._revision_key(snapshot.revision),
                self.l2_ttl_seconds,
                json.dumps(
                    {
                        "revision": snapshot.revision,
                        "groups": snapshot.groups,
                        "database_initialized": snapshot.database_initialized,
                    }
                ),
            )
        except Exception as exc:
            logger.debug("failed to write route group runtime cache into redis: %s", exc)
            return False
        return True

    def _revision_key(self, revision: int) -> str:
        return f"{self.cache_key}:r{revision}"

    @staticmethod
    def _copy_snapshot(snapshot: RouteGroupRuntimeSnapshot) -> RouteGroupRuntimeSnapshot:
        return RouteGroupRuntimeSnapshot(
            revision=snapshot.revision,
            groups=deepcopy(snapshot.groups),
            database_initialized=snapshot.database_initialized,
        )


def route_groups_from_config(cfg: AppConfig) -> list[dict[str, Any]]:
    return [item.model_dump(mode="python") for item in cfg.router_settings.route_groups]


async def load_route_group_snapshot(
    repository: RouteGroupRepository | None,
    cfg: AppConfig,
    route_group_cache: RouteGroupRuntimeCache | None = None,
    *,
    allow_config_fallback: bool = True,
) -> tuple[RouteGroupRuntimeSnapshot, str]:
    result = await load_route_group_snapshot_result(
        repository,
        cfg,
        route_group_cache,
        allow_config_fallback=allow_config_fallback,
    )
    return result.snapshot, result.compatibility_source


async def load_route_group_snapshot_result(
    repository: RouteGroupRepository | None,
    cfg: AppConfig,
    route_group_cache: RouteGroupRuntimeCache | None = None,
    *,
    allow_config_fallback: bool = True,
) -> RouteGroupSnapshotLoadResult:
    if repository is None:
        return RouteGroupSnapshotLoadResult(
            snapshot=RouteGroupRuntimeSnapshot(
                revision=0,
                groups=route_groups_from_config(cfg),
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
    except Exception as exc:
        if not allow_config_fallback:
            raise
        logger.warning("failed to load route groups from db, falling back to config: %s", exc)
        return RouteGroupSnapshotLoadResult(
            snapshot=RouteGroupRuntimeSnapshot(
                revision=0,
                groups=route_groups_from_config(cfg),
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
            groups=route_groups_from_config(cfg),
        ),
        source="config_db_empty",
        database_available=True,
        requires_reconciliation=False,
    )


async def load_route_groups(
    repository: RouteGroupRepository | None,
    cfg: AppConfig,
    route_group_cache: RouteGroupRuntimeCache | None = None,
) -> tuple[list[dict[str, Any]], str]:
    snapshot, source = await load_route_group_snapshot(repository, cfg, route_group_cache)
    return snapshot.groups, source
