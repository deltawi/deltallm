from __future__ import annotations

import pytest

from src.services.route_group_refresh import refresh_route_group_runtime


class _Cache:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.calls = 0

    async def invalidate(self) -> bool:
        self.calls += 1
        return self.result


class _Reloader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def reload_route_groups(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("reload unavailable")


class _Invalidation:
    def __init__(self, result: bool = True) -> None:
        self.redis = object()
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    async def notify(self, *targets: str) -> bool:
        self.calls.append(targets)
        return self.result


@pytest.mark.asyncio
async def test_refresh_reports_post_commit_degradation_without_raising() -> None:
    cache = _Cache(result=False)
    reloader = _Reloader(fail=True)
    invalidation = _Invalidation(result=False)

    result = await refresh_route_group_runtime(
        cache=cache,
        reloader=reloader,
        invalidation=invalidation,
    )

    assert result.warnings == (
        "Mutation committed, but local route-group runtime refresh failed",
        "Mutation committed, but cross-replica route-group invalidation failed",
    )
    assert cache.calls == 0
    assert reloader.calls == 1
    assert invalidation.calls == [("prompt", "route_groups")]


@pytest.mark.asyncio
async def test_refresh_success_has_no_warning() -> None:
    result = await refresh_route_group_runtime(
        cache=_Cache(),
        reloader=_Reloader(),
        invalidation=_Invalidation(),
    )

    assert result.warnings == ()
