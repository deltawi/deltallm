from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Callable, Protocol

from src.models.organization_lifecycle import TeamOrganizationLifecycle

logger = logging.getLogger(__name__)


class OrganizationLifecycleRepository(Protocol):
    async def lifecycle_generation(self) -> int: ...

    async def organization_lifecycle_state(self, organization_id: str) -> str | None: ...

    async def team_organization_lifecycle(
        self,
        team_id: str,
    ) -> TeamOrganizationLifecycle | None: ...


class OrganizationLifecycleUnavailable(RuntimeError):
    """Raised when authoritative lifecycle state cannot be checked safely."""


class OrganizationInactive(RuntimeError):
    def __init__(self, organization_id: str, lifecycle_state: str) -> None:
        super().__init__(f"organization {organization_id} is {lifecycle_state}")
        self.organization_id = organization_id
        self.lifecycle_state = lifecycle_state


@dataclass(frozen=True, slots=True)
class _LifecycleCacheEntry:
    state: str
    generation: int


@dataclass(frozen=True, slots=True)
class _TeamLifecycleCacheEntry:
    scope: TeamOrganizationLifecycle | None
    generation: int


@dataclass(frozen=True, slots=True)
class OrganizationLifecycleHealth:
    initialized: bool
    fresh: bool
    generation: int | None
    last_success_age_seconds: float | None
    last_error: str | None


class OrganizationLifecycleAuthorizer:
    """Bounded process cache over PostgreSQL-backed organization lifecycle state."""

    def __init__(
        self,
        repository: OrganizationLifecycleRepository,
        *,
        max_staleness_seconds: float = 3.0,
        max_entries: int = 10_000,
        lock_stripes: int = 64,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.repository = repository
        self.max_staleness_seconds = max(0.05, float(max_staleness_seconds))
        self.max_entries = max(1, int(max_entries))
        self.poll_interval_seconds = max(0.05, self.max_staleness_seconds / 2)
        self._clock = clock
        self._entries: OrderedDict[str, _LifecycleCacheEntry] = OrderedDict()
        self._team_entries: OrderedDict[str, _TeamLifecycleCacheEntry] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._refresh_locks = tuple(asyncio.Lock() for _ in range(max(1, lock_stripes)))
        self._current_generation: int | None = None
        self._generation_refreshed_at = 0.0
        self._last_refresh_error: str | None = "not_initialized"
        self._stop_event = asyncio.Event()

    async def initialize(self) -> None:
        await self.refresh_generation()

    def stop(self) -> None:
        self._stop_event.set()

    def health_snapshot(self) -> OrganizationLifecycleHealth:
        initialized = self._current_generation is not None
        age = max(0.0, self._clock() - self._generation_refreshed_at) if initialized else None
        return OrganizationLifecycleHealth(
            initialized=initialized,
            fresh=bool(initialized and age is not None and age <= self.max_staleness_seconds),
            generation=self._current_generation,
            last_success_age_seconds=age,
            last_error=self._last_refresh_error,
        )

    def is_ready(self) -> bool:
        return self.health_snapshot().fresh

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                try:
                    await self.refresh_generation()
                except OrganizationLifecycleUnavailable:
                    logger.exception("organization lifecycle generation refresh failed")

    async def refresh_generation(self) -> int:
        try:
            generation = max(0, int(await self.repository.lifecycle_generation()))
        except Exception as exc:
            async with self._cache_lock:
                self._last_refresh_error = type(exc).__name__
            raise OrganizationLifecycleUnavailable(
                "organization lifecycle generation unavailable"
            ) from exc
        async with self._cache_lock:
            self._current_generation = generation
            self._generation_refreshed_at = self._clock()
            self._last_refresh_error = None
        return generation

    async def require_active(self, organization_id: str | None) -> None:
        normalized_id = str(organization_id or "").strip()
        if not normalized_id:
            return
        generation = await self._fresh_generation()
        state = await self._get_state(normalized_id, generation=generation)
        if state != "active":
            raise OrganizationInactive(normalized_id, state or "missing")

    async def require_active_scope(
        self,
        *,
        organization_id: str | None,
        team_id: str | None,
    ) -> str | None:
        """Resolve team ownership from PostgreSQL and require an active organization."""

        normalized_organization_id = str(organization_id or "").strip() or None
        normalized_team_id = str(team_id or "").strip() or None
        if normalized_team_id is None:
            await self.require_active(normalized_organization_id)
            return normalized_organization_id

        generation = await self._fresh_generation()
        team_scope = await self._get_team_scope(normalized_team_id, generation=generation)
        if team_scope is None:
            raise OrganizationInactive(
                normalized_organization_id or normalized_team_id,
                "missing_team",
            )
        resolved_organization_id = team_scope.organization_id
        if (
            normalized_organization_id is not None
            and normalized_organization_id != resolved_organization_id
        ):
            raise OrganizationInactive(normalized_organization_id, "scope_mismatch")
        if resolved_organization_id is None:
            return None
        if team_scope.lifecycle_state != "active":
            raise OrganizationInactive(
                resolved_organization_id,
                team_scope.lifecycle_state,
            )
        await self._store(
            resolved_organization_id,
            team_scope.lifecycle_state,
            generation=generation,
        )
        return resolved_organization_id

    async def remember_state(
        self,
        organization_id: str,
        state: str,
        *,
        generation: int | None = None,
    ) -> None:
        normalized_id = str(organization_id or "").strip()
        normalized_state = str(state or "").strip().lower()
        if not normalized_id or not normalized_state:
            return
        current_generation = await self._fresh_generation()
        snapshot_generation = current_generation if generation is None else int(generation)
        if snapshot_generation != current_generation:
            return
        await self._store(normalized_id, normalized_state, generation=current_generation)

    async def remember_scope(
        self,
        *,
        organization_id: str | None,
        team_id: str | None,
        state: str,
        generation: int,
    ) -> None:
        normalized_organization_id = str(organization_id or "").strip() or None
        normalized_team_id = str(team_id or "").strip() or None
        normalized_state = str(state or "").strip().lower()
        if normalized_organization_id is None or not normalized_state:
            return
        current_generation = await self._fresh_generation()
        if int(generation) != current_generation:
            return
        await self._store(
            normalized_organization_id,
            normalized_state,
            generation=current_generation,
        )
        if normalized_team_id is not None:
            await self._store_team_scope(
                normalized_team_id,
                TeamOrganizationLifecycle(
                    organization_id=normalized_organization_id,
                    lifecycle_state=normalized_state,
                ),
                generation=current_generation,
            )

    async def invalidate(self, organization_id: str) -> None:
        normalized_id = str(organization_id or "").strip()
        if not normalized_id:
            return
        async with self._cache_lock:
            self._entries.pop(normalized_id, None)

    async def _get_state(self, organization_id: str, *, generation: int) -> str | None:
        cached = await self._cached_state(organization_id, generation=generation)
        if cached is not None:
            return cached

        refresh_lock = self._refresh_locks[hash(organization_id) % len(self._refresh_locks)]
        async with refresh_lock:
            current_generation = await self._fresh_generation()
            cached = await self._cached_state(
                organization_id,
                generation=current_generation,
            )
            if cached is not None:
                return cached
            try:
                state = await self.repository.organization_lifecycle_state(organization_id)
            except Exception as exc:
                raise OrganizationLifecycleUnavailable(
                    "organization lifecycle store unavailable"
                ) from exc
            normalized_state = str(state).strip().lower() if state is not None else "missing"
            await self._store(
                organization_id,
                normalized_state,
                generation=current_generation,
            )
            return normalized_state

    async def _get_team_scope(
        self,
        team_id: str,
        *,
        generation: int,
    ) -> TeamOrganizationLifecycle | None:
        cached, found = await self._cached_team_scope(team_id, generation=generation)
        if found:
            return cached

        refresh_lock = self._refresh_locks[hash(team_id) % len(self._refresh_locks)]
        async with refresh_lock:
            current_generation = await self._fresh_generation()
            cached, found = await self._cached_team_scope(
                team_id,
                generation=current_generation,
            )
            if found:
                return cached
            try:
                scope = await self.repository.team_organization_lifecycle(team_id)
            except Exception as exc:
                raise OrganizationLifecycleUnavailable(
                    "team organization lifecycle store unavailable"
                ) from exc
            await self._store_team_scope(
                team_id,
                scope,
                generation=current_generation,
            )
            return scope

    async def _fresh_generation(self) -> int:
        async with self._cache_lock:
            generation = self._current_generation
            age = self._clock() - self._generation_refreshed_at
        if generation is None or age > self.max_staleness_seconds:
            raise OrganizationLifecycleUnavailable("organization lifecycle generation is stale")
        return generation

    async def _cached_state(self, organization_id: str, *, generation: int) -> str | None:
        async with self._cache_lock:
            entry = self._entries.get(organization_id)
            if entry is None:
                return None
            if entry.generation != generation:
                return None
            self._entries.move_to_end(organization_id)
            return entry.state

    async def _cached_team_scope(
        self,
        team_id: str,
        *,
        generation: int,
    ) -> tuple[TeamOrganizationLifecycle | None, bool]:
        async with self._cache_lock:
            entry = self._team_entries.get(team_id)
            if entry is None or entry.generation != generation:
                return None, False
            self._team_entries.move_to_end(team_id)
            return entry.scope, True

    async def _store(self, organization_id: str, state: str, *, generation: int) -> None:
        entry = _LifecycleCacheEntry(state=state, generation=generation)
        async with self._cache_lock:
            self._entries[organization_id] = entry
            self._entries.move_to_end(organization_id)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    async def _store_team_scope(
        self,
        team_id: str,
        scope: TeamOrganizationLifecycle | None,
        *,
        generation: int,
    ) -> None:
        entry = _TeamLifecycleCacheEntry(scope=scope, generation=generation)
        async with self._cache_lock:
            self._team_entries[team_id] = entry
            self._team_entries.move_to_end(team_id)
            while len(self._team_entries) > self.max_entries:
                self._team_entries.popitem(last=False)


__all__ = [
    "OrganizationInactive",
    "OrganizationLifecycleAuthorizer",
    "OrganizationLifecycleHealth",
    "OrganizationLifecycleRepository",
    "OrganizationLifecycleUnavailable",
    "TeamOrganizationLifecycle",
]
