from __future__ import annotations

import pytest

from src.services.organization_lifecycle import (
    OrganizationInactive,
    OrganizationLifecycleAuthorizer,
    OrganizationLifecycleUnavailable,
    TeamOrganizationLifecycle,
)


class _Repository:
    def __init__(self, states: dict[str, str | None]) -> None:
        self.states = states
        self.generation = 0
        self.generation_calls = 0
        self.state_calls = 0
        self.team_calls = 0
        self.team_scopes: dict[str, TeamOrganizationLifecycle | None] = {}
        self.generation_error: Exception | None = None
        self.state_error: Exception | None = None

    async def lifecycle_generation(self) -> int:
        self.generation_calls += 1
        if self.generation_error is not None:
            raise self.generation_error
        return self.generation

    async def organization_lifecycle_state(self, organization_id: str) -> str | None:
        self.state_calls += 1
        if self.state_error is not None:
            raise self.state_error
        return self.states.get(organization_id)

    async def team_organization_lifecycle(
        self,
        team_id: str,
    ) -> TeamOrganizationLifecycle | None:
        self.team_calls += 1
        if self.state_error is not None:
            raise self.state_error
        return self.team_scopes.get(team_id)


@pytest.mark.asyncio
async def test_authorizer_invalidates_cached_state_when_generation_changes() -> None:
    repository = _Repository({"org-1": "active"})
    authorizer = OrganizationLifecycleAuthorizer(repository)

    await authorizer.initialize()
    await authorizer.require_active("org-1")
    await authorizer.require_active("org-1")
    assert repository.state_calls == 1

    repository.states["org-1"] = "deletion_pending"
    repository.generation += 1
    await authorizer.refresh_generation()
    with pytest.raises(OrganizationInactive) as exc_info:
        await authorizer.require_active("org-1")

    assert exc_info.value.lifecycle_state == "deletion_pending"
    assert repository.state_calls == 2


@pytest.mark.asyncio
async def test_authorizer_invalidation_forces_authoritative_refresh() -> None:
    repository = _Repository({"org-1": "active"})
    authorizer = OrganizationLifecycleAuthorizer(repository)

    await authorizer.initialize()
    await authorizer.require_active("org-1")
    repository.states["org-1"] = "deletion_pending"
    await authorizer.invalidate("org-1")

    with pytest.raises(OrganizationInactive):
        await authorizer.require_active("org-1")
    assert repository.state_calls == 2


@pytest.mark.asyncio
async def test_authorizer_fails_closed_when_store_is_unavailable() -> None:
    repository = _Repository({})
    repository.generation_error = RuntimeError("database unavailable")
    authorizer = OrganizationLifecycleAuthorizer(repository)

    with pytest.raises(OrganizationLifecycleUnavailable):
        await authorizer.initialize()


@pytest.mark.asyncio
async def test_authorizer_fails_closed_when_state_lookup_is_unavailable() -> None:
    repository = _Repository({})
    authorizer = OrganizationLifecycleAuthorizer(repository)
    await authorizer.initialize()
    repository.state_error = RuntimeError("database unavailable")

    with pytest.raises(OrganizationLifecycleUnavailable):
        await authorizer.require_active("org-1")


@pytest.mark.asyncio
async def test_authorizer_fails_closed_when_generation_refresh_is_stale() -> None:
    clock = [10.0]
    repository = _Repository({"org-1": "active"})
    authorizer = OrganizationLifecycleAuthorizer(
        repository,
        max_staleness_seconds=0.05,
        clock=lambda: clock[0],
    )

    await authorizer.initialize()
    await authorizer.require_active("org-1")
    clock[0] += 0.06

    with pytest.raises(OrganizationLifecycleUnavailable):
        await authorizer.require_active("org-1")
    assert repository.generation_calls == 1
    assert repository.state_calls == 1
    assert authorizer.is_ready() is False


@pytest.mark.asyncio
async def test_authorizer_health_tracks_success_and_safe_error_metadata() -> None:
    clock = [10.0]
    repository = _Repository({})
    authorizer = OrganizationLifecycleAuthorizer(
        repository,
        max_staleness_seconds=1.0,
        clock=lambda: clock[0],
    )

    assert authorizer.is_ready() is False
    await authorizer.initialize()
    assert authorizer.health_snapshot().fresh is True

    repository.generation_error = RuntimeError("secret database detail")
    with pytest.raises(OrganizationLifecycleUnavailable):
        await authorizer.refresh_generation()
    snapshot = authorizer.health_snapshot()
    assert snapshot.fresh is True
    assert snapshot.last_error == "RuntimeError"

    clock[0] += 1.01
    assert authorizer.is_ready() is False


@pytest.mark.asyncio
async def test_api_key_snapshot_avoids_per_organization_database_read() -> None:
    repository = _Repository({"org-1": "active"})
    repository.generation = 7
    authorizer = OrganizationLifecycleAuthorizer(repository)

    await authorizer.initialize()
    await authorizer.remember_state("org-1", "active", generation=7)
    await authorizer.require_active("org-1")

    assert repository.generation_calls == 1
    assert repository.state_calls == 0


@pytest.mark.asyncio
async def test_authorizer_bounds_cache_entries() -> None:
    repository = _Repository({"org-1": "active", "org-2": "active"})
    authorizer = OrganizationLifecycleAuthorizer(repository, max_entries=1)

    await authorizer.initialize()
    await authorizer.require_active("org-1")
    await authorizer.require_active("org-2")
    await authorizer.require_active("org-1")

    assert repository.state_calls == 3


@pytest.mark.asyncio
async def test_team_scope_is_resolved_once_per_generation() -> None:
    repository = _Repository({"org-1": "active"})
    repository.team_scopes["team-1"] = TeamOrganizationLifecycle("org-1", "active")
    authorizer = OrganizationLifecycleAuthorizer(repository)
    await authorizer.initialize()

    assert await authorizer.require_active_scope(organization_id=None, team_id="team-1") == "org-1"
    assert await authorizer.require_active_scope(organization_id=None, team_id="team-1") == "org-1"

    assert repository.team_calls == 1


@pytest.mark.asyncio
async def test_team_scope_rejects_inactive_and_mismatched_organizations() -> None:
    repository = _Repository({})
    repository.team_scopes["team-1"] = TeamOrganizationLifecycle(
        "org-1",
        "deletion_pending",
    )
    authorizer = OrganizationLifecycleAuthorizer(repository)
    await authorizer.initialize()

    with pytest.raises(OrganizationInactive):
        await authorizer.require_active_scope(organization_id=None, team_id="team-1")

    repository.team_scopes["team-2"] = TeamOrganizationLifecycle("org-2", "active")
    with pytest.raises(OrganizationInactive) as exc_info:
        await authorizer.require_active_scope(organization_id="org-1", team_id="team-2")
    assert exc_info.value.lifecycle_state == "scope_mismatch"
