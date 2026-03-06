from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import time
from typing import Any

from src.config import AppConfig
from src.db.route_groups import RouteGroupRepository

logger = logging.getLogger(__name__)
ROUTE_GROUP_RUNTIME_CACHE_KEY = "deltallm:routegroup:v1:runtime"


@dataclass
class _RuntimeCacheEntry:
    groups: list[dict[str, Any]]
    expires_at: float


class RouteGroupRuntimeCache:
    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        l1_ttl_seconds: int = 30,
        l2_ttl_seconds: int = 300,
    ) -> None:
        self.redis = redis_client
        self.l1_ttl_seconds = max(1, int(l1_ttl_seconds))
        self.l2_ttl_seconds = max(1, int(l2_ttl_seconds))
        self._l1_entry: _RuntimeCacheEntry | None = None

    async def get_groups(self, repository: RouteGroupRepository) -> tuple[list[dict[str, Any]], str]:
        l1_groups = self._read_l1()
        if l1_groups is not None:
            return l1_groups, "l1_cache"

        l2_groups = await self._read_l2()
        if l2_groups is not None:
            self._write_l1(l2_groups)
            return deepcopy(l2_groups), "l2_cache"

        groups = await repository.list_runtime_groups()
        if groups:
            self._write_l1(groups)
            await self._write_l2(groups)
        return deepcopy(groups), "db"

    async def invalidate(self) -> None:
        self._l1_entry = None
        if self.redis is None:
            return
        try:
            await self.redis.delete(ROUTE_GROUP_RUNTIME_CACHE_KEY)
        except Exception as exc:
            logger.debug("failed to invalidate route group runtime cache: %s", exc)

    def _read_l1(self) -> list[dict[str, Any]] | None:
        entry = self._l1_entry
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._l1_entry = None
            return None
        return deepcopy(entry.groups)

    def _write_l1(self, groups: list[dict[str, Any]]) -> None:
        self._l1_entry = _RuntimeCacheEntry(
            groups=deepcopy(groups),
            expires_at=time.monotonic() + self.l1_ttl_seconds,
        )

    async def _read_l2(self) -> list[dict[str, Any]] | None:
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(ROUTE_GROUP_RUNTIME_CACHE_KEY)
            if not raw:
                return None
            payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception as exc:
            logger.debug("failed to read route group runtime cache from redis: %s", exc)
            return None
        if not isinstance(payload, list):
            return None
        return [item for item in payload if isinstance(item, dict)]

    async def _write_l2(self, groups: list[dict[str, Any]]) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.setex(
                ROUTE_GROUP_RUNTIME_CACHE_KEY,
                self.l2_ttl_seconds,
                json.dumps(groups),
            )
        except Exception as exc:
            logger.debug("failed to write route group runtime cache into redis: %s", exc)


def route_groups_from_config(cfg: AppConfig) -> list[dict[str, Any]]:
    return [item.model_dump(mode="python") for item in cfg.router_settings.route_groups]


async def load_route_groups(
    repository: RouteGroupRepository | None,
    cfg: AppConfig,
    route_group_cache: RouteGroupRuntimeCache | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if repository is None:
        return route_groups_from_config(cfg), "config"

    try:
        if route_group_cache is None:
            groups = await repository.list_runtime_groups()
            source = "db"
        else:
            groups, source = await route_group_cache.get_groups(repository)
    except Exception as exc:
        logger.warning("failed to load route groups from db, falling back to config: %s", exc)
        return route_groups_from_config(cfg), "config"

    if groups:
        return groups, source
    return route_groups_from_config(cfg), "config"
